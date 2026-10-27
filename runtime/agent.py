"""Board-side managed dataset job. Uses only system Python's standard library."""
import fcntl
import hashlib
import json
import math
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
    (job/'logs').mkdir(exist_ok=True)
    with (job/'logs/supervisor.log').open('ab') as log:
        p=subprocess.Popen(['/usr/bin/python3',__file__,'run',str(job),str(guard.fileno())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,pass_fds=(guard.fileno(),))
    write_json(root/'latest.json',dict(job=str(job),pid=p.pid));print(json.dumps(dict(job=str(job),pid=p.pid)))


def run(job,lockfd):
    job=Path(job);cfg=json.loads((job/'config.json').read_text());install=Path(cfg['install']);processes={};handles=[];started=time.monotonic();failure=None;requested_stop=False
    capture_mode=cfg.get('mode','dataset').startswith('capture');calibration_mode=cfg.get('mode')=='calibrate'
    status=dict(run_id=job.name,phase='starting',algorithm='running',transfer='pending',started=time.time(),pid=os.getpid())
    for d in ['logs','monitor','algorithm/Log/pcd','algorithm/Log/image','algorithm/Log/result','algorithm/Log/ref_cur_combine']:(job/d).mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=os.environ.copy();env.update(ROS_MASTER_URI=f'http://127.0.0.1:{port}',ROS_IP='127.0.0.1',ROS_HOME=str(job/'ros-home'),ROS_LOG_DIR=str(job/'logs/ros'),LIVO_OUTPUT_DIR=str(job/'algorithm')+'/')
    if calibration_mode:env['LIVO_HEADLESS']='1'
    status['master_uri']=env['ROS_MASTER_URI'];resources={}
    rt=cfg.get('runtime',dict(livo_cpus=[4,5,6,7],opencv_threads=1))
    cpu_policies=list(Path('/sys/devices/system/cpu/cpufreq').glob('policy*'))
    original_governors={}
    def governors(values):
        code='import json,sys;from pathlib import Path;[(Path(p).write_text(v)) for p,v in json.loads(sys.argv[1]).items()]'
        subprocess.run(['sudo','-n','/usr/bin/python3','-c',code,json.dumps(values)],check=True,timeout=10)
    def state(phase,**kw):
        if capture_mode:kw.setdefault('progress',monitor())
        status.update(phase=phase,heartbeat=time.time(),elapsed_s=time.monotonic()-started,**kw);write_json(job/'state.json',status)
    def spawn(name,args,ros=True):
        log=(job/'logs'/f'{name}.log').open('ab');handles.append(log)
        cmd=['bash','-c','source "$1/env.sh"; shift; exec "$@"','livo',str(install),*map(str,args)] if ros else list(map(str,args))
        child_env=env.copy()
        if name=='livo':
            child_env.update(OMP_WAIT_POLICY='PASSIVE',OPENBLAS_NUM_THREADS='1')
            if rt['livo_cpus']:child_env['GOMP_CPU_AFFINITY']=' '.join(map(str,rt['livo_cpus']))
            else:child_env.pop('GOMP_CPU_AFFINITY',None)
        p=subprocess.Popen(cmd,cwd=job,env=child_env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);processes[name]=p;return p
    def ros(args):
        subprocess.run(['bash','-c','source "$1/env.sh"; shift; exec "$@"','livo',str(install),*map(str,args)],env=env,stdout=subprocess.DEVNULL,check=True)
    def monitor():
        try:return json.loads((job/'monitor/state.json').read_text())
        except (OSError,ValueError):return {}
    def alive():
        for name,p in processes.items():
            try:
                fields=Path(f'/proc/{p.pid}/stat').read_text().rsplit(')',1)[1].split()
                mem=Path(f'/proc/{p.pid}/status').read_text().splitlines()
                rss=next(int(x.split()[1]) for x in mem if x.startswith('VmRSS:'))
                r=resources.setdefault(name,dict(peak_rss_kib=0))
                r.update(cpu_s=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),rss_kib=rss,peak_rss_kib=max(rss,r['peak_rss_kib']))
            except (OSError,StopIteration,ValueError):pass
        status['resources']=resources
        status['cpu_freq_khz']={p.name:int((p/'scaling_cur_freq').read_text()) for p in cpu_policies}
        if (job/'stop').exists():raise InterruptedError('User requested stop')
        if shutil.disk_usage(job).free < cfg['min_free_gib']*2**30:raise RuntimeError('Insufficient free disk space')
        if time.monotonic()-started>cfg['timeout_s']:raise RuntimeError('Task timeout')
        for name,p in processes.items():
            if name not in {'play','calibration'} and p.poll() is not None:raise RuntimeError(f'{name} exited unexpectedly: {p.returncode}')
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
        if not set(rt['livo_cpus']).issubset(os.sched_getaffinity(0)):raise ValueError('Configured LIVO CPU is unavailable on this board')
        write_json(job/'cpu-environment.json',dict(affinity=sorted(os.sched_getaffinity(0)),runtime=rt,policies={p.name:{k:(p/k).read_text().strip() for k in ['scaling_governor','scaling_min_freq','scaling_max_freq']} for p in cpu_policies}))
        spawn('monitor',['/usr/bin/python3',install/'runtime/monitor.py',job,str(cfg['monitoring']['repeat_notice_s'])],False)
        if rt.get('cpu_governor'):
            original_governors={str(p/'scaling_governor'):(p/'scaling_governor').read_text().strip() for p in cpu_policies}
            if not original_governors:raise RuntimeError('CPU frequency policies unavailable; set runtime.cpu_governor to null')
            state('configuring CPU');governors({p:rt['cpu_governor'] for p in original_governors})
        state('starting ROS');spawn('master',['roscore','-p',str(port)])
        master=xmlrpc.client.ServerProxy(env['ROS_MASTER_URI']);deadline=time.monotonic()+20
        while True:
            alive()
            try:
                if master.getPid('/livo-supervisor')[0]==1:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('ROS master startup timeout')
            time.sleep(.2)
        if capture_mode:
            import capture
            capture.run(cfg,job,install,spawn,ros,alive,state,processes,master)
            return
        if calibration_mode:
            import calibration
            calibration.run(cfg,job,install,spawn,ros,alive,state,processes)
            return
        ros(['rosparam','set','/use_sim_time','true']);ros(['rosparam','load',job/'algorithm.yaml']);ros(['rosparam','load',job/'camera.yaml','/laserMapping'])
        image_key=cfg.get('image_input','compressed_image');input_name='raw' if cfg.get('image_transport')=='raw' else 'compressed'
        spawn('decode',[install/'bin/livo_image_decode',input_name+':='+cfg['topics'][image_key],'image:='+cfg['topics']['image']])
        spawn('livo',[install/'bin/fastlivo_mapping'])
        state('waiting for subscribers');deadline=time.monotonic()+30
        while True:
            alive();pubs,subs,services=master.getSystemState('/livo-supervisor')[2];submap=dict(subs)
            required=[cfg['topics'][k] for k in ['lidar','imu','image',image_key]]
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
    except InterruptedError as e:requested_stop=True;status['stop_reason']=str(e)
    except Exception as e:failure=str(e)
    finally:
        state('saving')
        # Stop producers before the algorithm. Master remains available for shutdown.
        for name in ['play','camera','livox','decode','livo','record','probe','calibration']:
            try:stop(name)
            except Exception as e:failure=(failure+'; ' if failure else '')+str(e)
        if capture_mode:
            try:ros(['python3',install/'runtime/capture_probe.py','verify',job])
            except Exception as e:failure=(failure+'; ' if failure else '')+'Bag validation failed: '+str(e)
        try:stop('master')
        except Exception as e:failure=(failure+'; ' if failure else '')+str(e)
        if original_governors:
            try:governors(original_governors);status['cpu_governor_restored']=True
            except Exception as e:failure=(failure+'; ' if failure else '')+'Failed to restore CPU governor: '+str(e)
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
        if not capture_mode and not calibration_mode and (not poses or not pcds):failure=(failure+'; ' if failure else '')+'Missing trajectory or map'
        if any(not all(math.isfinite(v) for v in row) for row in poses):failure=(failure+'; ' if failure else '')+'Non-finite trajectory'
        if poses and not requested_stop and cfg['expected_end_s']-poses[-1][0]>cfg['tail_tolerance_s']:failure=(failure+'; ' if failure else '')+'Trajectory does not cover sequence end'
        exits={name:p.returncode for name,p in processes.items()}
        # ROS's normal SIGINT shutdown may return 0 or -SIGINT for helper tools.
        if not capture_mode and not calibration_mode and exits.get('livo')!=0:failure=(failure+'; ' if failure else '')+'LIVO did not exit cleanly'
        if capture_mode and exits.get('record')!=0:failure=(failure+'; ' if failure else '')+'Recorder did not exit cleanly'
        status.update(algorithm='failed' if failure else 'stopped' if requested_stop else 'complete',reason=failure,exits=exits,progress=m,poses=len(poses),map_chunks=len(pcds),trajectory_start=poses[0][0] if poses else None,trajectory_end=poses[-1][0] if poses else None)
        write_json(job/'metrics.json',dict(processes=resources,monitor=m,poses=len(poses),map_chunks=len(pcds),unprocessed_tail_s=status.get('unprocessed_tail_s')))
        state('finished');write_json(job/'summary.json',status)
        # Generate after all writers have stopped. state/summary stay immutable until collection.
        files={str(p.relative_to(job)):digest(p) for p in job.rglob('*') if p.is_file() and p.name!='files.json'}
        write_json(job/'files.json',files)
        (job/'complete').touch()
        os.close(int(lockfd))


if __name__=='__main__':
    if sys.argv[1]=='start':start(sys.argv[2])
    elif sys.argv[1]=='run':run(sys.argv[2],sys.argv[3])
