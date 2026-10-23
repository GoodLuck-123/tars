"""Board-side managed dataset job. Uses only system Python's standard library."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time
import xmlrpc.client
from monitor import write_json


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def start(job):
    job=Path(job).resolve();cfg=json.loads((job/'config.json').read_text());root=Path(cfg['workspace'])
    guard=(root/'run.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # Transfer lock ownership via inherited descriptor, so concurrent starts cannot race.
    os.set_inheritable(guard.fileno(),True)
    with (job/'supervisor.log').open('ab') as log:
        p=subprocess.Popen(['/usr/bin/python3',__file__,'run',str(job),str(guard.fileno())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,pass_fds=(guard.fileno(),))
    write_json(root/'latest.json',dict(job=str(job),pid=p.pid));print(json.dumps(dict(job=str(job),pid=p.pid)))


def run(job,lockfd):
    job=Path(job);cfg=json.loads((job/'config.json').read_text());install=Path(cfg['install']);processes={};handles=[];started=time.monotonic();failure=None;requested_stop=False
    status=dict(run_id=job.name,phase='starting',algorithm='running',transfer='pending',started=time.time(),pid=os.getpid())
    for d in ['logs','monitor','algorithm/Log/pcd','algorithm/Log/image','algorithm/Log/result','algorithm/Log/ref_cur_combine']:(job/d).mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=os.environ.copy();env.update(ROS_MASTER_URI=f'http://127.0.0.1:{port}',ROS_IP='127.0.0.1',ROS_LOG_DIR=str(job/'logs/ros'),LIVO_OUTPUT_DIR=str(job/'algorithm')+'/')
    def state(phase,**kw):
        status.update(phase=phase,heartbeat=time.time(),elapsed_s=time.monotonic()-started,**kw);write_json(job/'state.json',status)
    def spawn(name,args,ros=True):
        log=(job/'logs'/f'{name}.log').open('ab');handles.append(log)
        cmd=['bash','-c','source "$1/env.sh"; shift; exec "$@"','livo',str(install),*map(str,args)] if ros else list(map(str,args))
        p=subprocess.Popen(cmd,cwd=job,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);processes[name]=p;return p
    def ros(args):
        subprocess.run(['bash','-c','source "$1/env.sh"; shift; exec "$@"','livo',str(install),*map(str,args)],env=env,stdout=subprocess.DEVNULL,check=True)
    def monitor():
        try:return json.loads((job/'monitor/state.json').read_text())
        except (OSError,ValueError):return {}
    def alive():
        if (job/'stop').exists():raise InterruptedError('User requested stop')
        if shutil.disk_usage(job).free < cfg['min_free_gib']*2**30:raise RuntimeError('Insufficient free disk space')
        if time.monotonic()-started>cfg['timeout_s']:raise RuntimeError('Task timeout')
        for name,p in processes.items():
            if name!='play' and p.poll() is not None:raise RuntimeError(f'{name} exited unexpectedly: {p.returncode}')
        m=monitor()
        if m and time.time()-m.get('heartbeat',0)>10:raise RuntimeError('Monitoring heartbeat expired')
        if status['phase']=='running' and m.get('progress_wall') and time.time()-m['progress_wall']>cfg['monitoring']['progress_stall_s']:raise RuntimeError('Algorithm processing stalled')
        if time.monotonic()>started+10 and not (job/'monitor/state.json').exists():raise RuntimeError('Monitoring unavailable')
    def stop(name):
        p=processes.get(name)
        if not p or p.poll() is not None:return
        os.killpg(p.pid,signal.SIGINT)
        try:p.wait(timeout=cfg['shutdown_timeout_s'])
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait();raise RuntimeError(f'{name} required SIGKILL')
    try:
        spawn('monitor',['/usr/bin/python3',install/'runtime/monitor.py',job,str(cfg['monitoring']['repeat_notice_s'])],False)
        state('starting ROS');spawn('master',['roscore','-p',str(port)])
        master=xmlrpc.client.ServerProxy(env['ROS_MASTER_URI']);deadline=time.monotonic()+20
        while True:
            alive()
            try:
                if master.getPid('/livo-supervisor')[0]==1:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('ROS master startup timeout')
            time.sleep(.2)
        ros(['rosparam','set','/use_sim_time','true']);ros(['rosparam','load',job/'algorithm.yaml']);ros(['rosparam','load',job/'camera.yaml','/laserMapping'])
        spawn('decode',[install/'bin/livo_image_decode','compressed:='+cfg['topics']['compressed_image'],'image:='+cfg['topics']['image']])
        spawn('livo',[install/'bin/fastlivo_mapping'])
        state('waiting for subscribers');deadline=time.monotonic()+30
        while True:
            alive();pubs,subs,services=master.getSystemState('/livo-supervisor')[2];submap=dict(subs)
            required=[cfg['topics'][k] for k in ['lidar','imu','image','compressed_image']]
            if all(submap.get(t) for t in required):break
            if time.monotonic()>deadline:raise RuntimeError('Input subscriptions not ready: '+repr(required))
            time.sleep(.2)
        args=['rosbag','play','--clock','--rate',str(cfg['rate']),'--start',str(cfg['start_s']),'--quiet']
        if cfg['duration_s'] is not None:args+=['--duration',str(cfg['duration_s'])]
        args+=[cfg['bag']];spawn('play',args);state('running')
        while processes['play'].poll() is None:
            alive();m=monitor();state('running',progress=m);time.sleep(.5)
        if processes['play'].returncode:raise RuntimeError('rosbag play failed')
        state('draining');deadline=time.monotonic()+cfg['drain_timeout_s'];last_stamp=None;changed=time.monotonic()
        while True:
            alive();m=monitor();stamp=m.get('processed_stamp',0)
            if stamp!=last_stamp:last_stamp=stamp;changed=time.monotonic()
            tail=cfg['expected_end_s']-stamp
            state('draining',progress=m,unprocessed_tail_s=tail)
            if stamp and tail<=cfg['tail_tolerance_s'] and time.monotonic()-changed>=3:break
            if time.monotonic()>deadline:raise RuntimeError(f'Drain timeout; remaining sensor-time tail {tail:.3f}s')
            time.sleep(.5)
        if m.get('visual_updates',0)<1:raise RuntimeError('No visual updates observed; LIO alone is not LIVO')
    except InterruptedError as e:requested_stop=True;failure=str(e)
    except Exception as e:failure=str(e)
    finally:
        state('saving')
        # Stop producers before the algorithm. Master remains available for shutdown.
        for name in ['play','decode','livo']:
            try:stop(name)
            except Exception as e:failure=(failure+'; ' if failure else '')+str(e)
        try:stop('master')
        except Exception as e:failure=(failure+'; ' if failure else '')+str(e)
        (job/'stop-monitor').touch()
        try:
            if 'monitor' in processes:processes['monitor'].wait(timeout=10)
        except subprocess.TimeoutExpired:
            stop('monitor');failure=(failure+'; ' if failure else '')+'Monitor did not finish'
        for f in handles:f.close()
        m=monitor();traj=job/'algorithm/Log/result/trajectory.txt';pcds=list((job/'algorithm/Log/pcd').glob('*.pcd'))
        poses=[]
        if traj.exists():
            for line in traj.read_text().splitlines():
                try:
                    row=list(map(float,line.split()))
                    if len(row)==8:poses.append(row)
                except ValueError:pass
        if not poses or not pcds:failure=(failure+'; ' if failure else '')+'Missing trajectory or map'
        exits={name:p.returncode for name,p in processes.items()}
        # ROS's normal SIGINT shutdown may return 0 or -SIGINT for helper tools.
        if exits.get('livo')!=0:failure=(failure+'; ' if failure else '')+'LIVO did not exit cleanly'
        status.update(algorithm='stopped' if requested_stop else 'failed' if failure else 'complete',reason=failure,exits=exits,progress=m,poses=len(poses),map_chunks=len(pcds),trajectory_start=poses[0][0] if poses else None,trajectory_end=poses[-1][0] if poses else None)
        state('finished');write_json(job/'summary.json',status)
        # Generate after all writers have stopped. state/summary stay immutable until collection.
        files={str(p.relative_to(job)):digest(p) for p in job.rglob('*') if p.is_file() and p.name not in {'files.json','supervisor.log'}}
        write_json(job/'files.json',files)
        os.close(int(lockfd))


if __name__=='__main__':
    if sys.argv[1]=='start':start(sys.argv[2])
    elif sys.argv[1]=='run':run(sys.argv[2],sys.argv[3])
