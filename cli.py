"""One entry point for local builds and managed RK3588 dataset jobs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid
import yaml
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'buildkit'))
from fetch import sha


def local(args,**kw):return subprocess.run(list(map(str,args)),check=True,**kw)


def config(path):
    path=Path(path).resolve()
    if not path.exists():raise RuntimeError(f'Create your configuration: cp {ROOT}/project.example.yaml {path}')
    c=yaml.safe_load(path.read_text())
    c.setdefault('runtime',dict(livo_cpus=[4,5,6,7],opencv_threads=1))
    rt=c['runtime']
    rt.setdefault('cpu_governor',None)
    if rt['cpu_governor'] not in {None,'performance'}:raise ValueError('runtime.cpu_governor must be null or performance')
    if not isinstance(rt['livo_cpus'],list) or any(type(n) is not int or n<0 for n in rt['livo_cpus']):raise ValueError('runtime.livo_cpus must be a list of CPU numbers')
    if type(rt['opencv_threads']) is not int or rt['opencv_threads']<1:raise ValueError('runtime.opencv_threads must be positive')
    if c['dataset']['rate']<=0 or c['build']['jobs']<1:raise ValueError('rate and build.jobs must be positive')
    if c['dataset']['start_s']<0 or (c['dataset']['duration_s'] is not None and c['dataset']['duration_s']<=0):raise ValueError('Invalid dataset interval')
    if len(c['camera']['intrinsics'])!=4 or len(c['camera']['distortion']) not in {4,5,8,12,14}:raise ValueError('Invalid camera model parameters')
    if c['camera']['scale']<=0 or min(c['camera']['width'],c['camera']['height'])<=0:raise ValueError('Invalid camera dimensions or scale')
    import numpy as np
    for key in ['R_camera_lidar','R_imu_lidar']:
        r=np.asarray(c['extrinsics'][key],dtype=float).reshape(3,3)
        if not np.isfinite(r).all() or not np.allclose(r.T@r,np.eye(3),atol=.01) or abs(np.linalg.det(r)-1)>.01:raise ValueError('Invalid rotation: '+key)
    c['_config_dir']=str(path.parent)
    for key in ['path']:
        p=Path(c['dataset'][key]).expanduser();c['dataset'][key]=str((path.parent/p).resolve())
    p=Path(c['results_dir']).expanduser();c['results_dir']=str((path.parent/p).resolve())
    if not isinstance(c['algorithm']['overrides'],dict):raise ValueError('algorithm.overrides must be a mapping')
    return c


def ssh(c,command,**kw):
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2','-p',str(c['board']['ssh_port']),c['board']['target'],command],check=True,**kw)


def remote(c,code,args=()):
    cmd='python3 - '+shlex.join(list(map(str,args)))
    return ssh(c,cmd,input=code,text=True,capture_output=True).stdout


def workspace(c):
    return remote(c,'import os,sys;p=os.path.abspath(os.path.expanduser(sys.argv[1]));assert p not in ["/",os.path.expanduser("~")],"Choose a dedicated workspace";print(p)',[c['board']['workspace']]).strip()


def sync(c,source,dest,receive=False):
    transport=shlex.join(['ssh','-p',str(c['board']['ssh_port']),'-o','BatchMode=yes','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2'])
    target=c['board']['target']+':'+dest
    local(['rsync','-a','--partial','--protect-args','--info=progress2','-e',transport,target if receive else source,source if receive else target],stdout=subprocess.DEVNULL)


def doctor(c):
    print('检查主机与板端…',flush=True)
    info=json.loads(remote(c,'import json,platform,shutil,os;print(json.dumps(dict(architecture=platform.machine(),python=platform.python_version(),free_gib=shutil.disk_usage(os.path.expanduser("~")).free/2**30,rsync=shutil.which("rsync"))))'))
    print(json.dumps(info))
    if info['architecture']!='aarch64' or not info['rsync']:raise RuntimeError('Board requires aarch64 and rsync')
    if info['free_gib']<c['run']['min_free_gib']:raise RuntimeError('Board disk space below configured reserve')
    p=Path(c['dataset']['path'])
    if not p.is_file():raise RuntimeError('Dataset not found: '+str(p))
    from rosbags.rosbag1 import Reader
    with Reader(p) as r:
        if c['dataset']['start_s']>=r.duration/1e9:raise ValueError('Dataset start is past EOF')
        found={x.topic:x.msgtype for x in r.connections}
        for key,typ in [('lidar','livox_ros_driver/msg/CustomMsg'),('imu','sensor_msgs/msg/Imu'),('compressed_image','sensor_msgs/msg/CompressedImage')]:
            if found.get(c['topics'][key])!=typ:raise RuntimeError(f'Invalid or missing {key} topic/type')
        print(f'数据集：{p.name}，{r.duration/1e9:.2f} 秒，{r.message_count} 条消息',flush=True)


def build(c):
    from fetch import packages
    # Optional cache seeds only accelerate pinned downloads; not runtime dependencies.
    for name,target in [('toolchain','toolchain'),('ros1-sdk','sysroot'),('pcl','sysroot')]:packages(name,ROOT/'.cache'/target,c['build'].get('seed_caches',[]))
    log=ROOT/'.cache/build.log';log.parent.mkdir(exist_ok=True)
    print('构建 ARM64 产物；详细日志：'+str(log),flush=True)
    with log.open('w') as f:
        result=subprocess.run([sys.executable,ROOT/'buildkit/build.py',str(c['build']['jobs'])],stdout=f,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError('Build failed; inspect '+str(log))
    with log.open('a') as f:
        result=subprocess.run([sys.executable,ROOT/'buildkit/bundle.py'],stdout=f,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError('Packaging failed; inspect '+str(log))
    release=json.loads((ROOT/'dist/release.json').read_text())
    print(f"构建完成：{release['id']}，{release['bytes']/2**20:.1f} MiB",flush=True)


def deploy(c):
    meta=ROOT/'dist/release.json'
    from bundle import input_fingerprint
    if not meta.exists() or json.loads(meta.read_text()).get('input_fingerprint')!=input_fingerprint():build(c)
    release=json.loads(meta.read_text());root=workspace(c)
    if root=='/' or root==str(Path.home()):raise ValueError('Refuse unsafe workspace')
    remote(c,'from pathlib import Path;import sys;p=Path(sys.argv[1]);[(p/x).mkdir(parents=True,exist_ok=True) for x in ["releases","incoming","runs","data"]]',[root])
    existing=remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"releases"/sys.argv[2]/"files.json").exists())',[root,release['id']]).strip()
    dest=root+'/releases/'+release['id']
    if existing=='True':
        remote(c,'import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/"runtime"));from install import verify;verify(p)',[dest])
    if existing!='True':
        print('部署运行包…',flush=True);archive=root+'/incoming/'+Path(release['path']).name;sync(c,release['path'],archive)
        code='''import hashlib,json,sys,tarfile,os,subprocess,shutil
from pathlib import Path
root,archive,ident,expected=map(str,sys.argv[1:]);root=Path(root);archive=Path(archive)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
stage=root/'releases'/('.staging-'+ident)
if stage.exists():shutil.rmtree(stage)
stage.mkdir()
with tarfile.open(archive) as t:
 for m in t.getmembers():
  assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and not m.isdev()
  assert not m.issym() and not m.islnk()
 t.extractall(stage)
p=stage/'install';sys.path.insert(0,str(p/'runtime'))
from install import verify
assert verify(p)==ident
subprocess.run(['bash','-ec','source \"$1/env.sh\"; python3 -c \"import rospy, rosbag, yaml\"; rosversion -d','verify',str(p)],check=True)
p.rename(root/'releases'/ident);stage.rmdir()
'''
        print(remote(c,code,[root,archive,release['id'],release['sha256']]).strip())
    remote(c,'from pathlib import Path;import sys,os;r=Path(sys.argv[1]);t=r/"current.new";t.unlink(missing_ok=True);t.symlink_to("releases/"+sys.argv[2]);os.replace(t,r/"current")',[root,release['id']])
    print('运行包已就绪：'+release['id'],flush=True);return root,dest


def prepare(c,job,install,root):
    from rosbags.rosbag1 import Reader
    p=Path(c['dataset']['path']);ident=sha(p);bag=root+'/data/'+ident+'.bag'
    present=remote(c,'from pathlib import Path;import sys;print(Path(sys.argv[1]).stat().st_size if Path(sys.argv[1]).exists() else -1)',[bag]).strip()
    if int(present)!=p.stat().st_size:print('上传数据集…',flush=True);sync(c,str(p),bag)
    actual=remote(c,'import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],"rb");[(h.update(b)) for b in iter(lambda:f.read(1048576),b"")];print(h.hexdigest())',[bag]).strip()
    if actual!=ident:raise RuntimeError('Remote dataset hash mismatch')
    with Reader(p) as r:
        start=r.start_time/1e9+c['dataset']['start_s'];end=min(r.end_time/1e9,start+c['dataset']['duration_s']) if c['dataset']['duration_s'] is not None else r.end_time/1e9
        input_info=dict(path=str(p),sha256=ident,start_s=start,end_s=end,topics=[dict(topic=x.topic,type=x.msgtype,count=x.msgcount) for x in r.connections])
    cfg=dict(workspace=root,install=install,bag=bag,rate=c['dataset']['rate'],start_s=c['dataset']['start_s'],duration_s=c['dataset']['duration_s'],expected_end_s=end,tail_tolerance_s=c['dataset']['tail_tolerance_s'],topics=c['topics'],monitoring=c['monitoring'],runtime=c['runtime'],**{k:c['run'][k] for k in ['timeout_s','shutdown_timeout_s','drain_timeout_s','min_free_gib']})
    upstream=yaml.safe_load((ROOT/'third_party/FAST-LIVO2/config/avia.yaml').read_text());upstream['common'].update(img_topic=c['topics']['image'],lid_topic=c['topics']['lidar'],imu_topic=c['topics']['imu'])
    upstream['runtime']=dict(opencv_threads=c['runtime']['opencv_threads'])
    upstream['time_offset'].update(img_time_offset=0.0,imu_time_offset=0.0)
    e=c['extrinsics'];upstream['extrin_calib']=dict(extrinsic_T=e['t_imu_lidar'],extrinsic_R=e['R_imu_lidar'],Rcl=e['R_camera_lidar'],Pcl=e['t_camera_lidar'])
    upstream['evo']=dict(seq_name='trajectory',pose_output_en=True);upstream['pcd_save'].update(pcd_save_en=True,interval=c['run']['map_chunk_frames']);upstream['image_save']['img_save_en']=False
    for section,values in c['algorithm']['overrides'].items():upstream.setdefault(section,{}).update(values)
    fx,fy,cx,cy=c['camera']['intrinsics'];w,h=c['camera']['width'],c['camera']['height'];scale=c['camera']['scale'];ow,oh=int(w*scale),int(h*scale)
    if min(ow,oh)<1:raise ValueError('Scaled camera dimensions must be positive')
    upstream['decoder']=dict(K=[fx,0,cx,0,fy,cy,0,0,1],D=c['camera']['distortion'],width=w,height=h,output_width=ow,output_height=oh)
    cam=dict(cam_model='Pinhole',cam_width=ow,cam_height=oh,scale=1.0,cam_fx=fx*ow/w,cam_fy=fy*oh/h,cam_cx=cx*ow/w,cam_cy=cy*oh/h,cam_d0=0.0,cam_d1=0.0,cam_d2=0.0,cam_d3=0.0)
    job.mkdir(parents=True)
    for name,value in [('config.json',cfg),('input.json',input_info)]: (job/name).write_text(json.dumps(value,indent=2)+'\n')
    for name,value in [('algorithm.yaml',upstream),('camera.yaml',cam),('config.resolved.yaml',c)]: (job/name).write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False))
    return cfg


def latest(c,run_id=None):
    root=workspace(c)
    if run_id:
        if Path(run_id).name!=run_id:raise ValueError('Invalid run ID')
        return root+'/runs/'+run_id
    return json.loads(remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"latest.json").read_text())',[root]))['job']


def state(c,job):return json.loads(remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"state.json").read_text())',[job]))


def status_line(s):
    m=s.get('progress',{})
    return f"{s['phase']} | {s.get('elapsed_s',0):.1f}s | 处理 {m.get('processed',0)} | 视觉更新 {m.get('visual_updates',0)} | WARN {m.get('warnings',0)} ERROR {m.get('errors',0)}"


def follow(c,job):
    last=None;seen={}
    while True:
        try:s=state(c,job)
        except subprocess.CalledProcessError:print('连接中断；板端任务继续，使用 status --follow 重连',flush=True);return None
        m=s.get('progress',{});line=status_line(s)
        if line!=last:print(line,flush=True);last=line
        for key,a in m.get('alerts',{}).items():
            if seen.get(key)!=a.get('last_notice'):
                print(f"[{a['level']}] {a['message']} (累计 {a['count']})",flush=True);seen[key]=a.get('last_notice')
        if s['phase']=='finished':
            ready=remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"files.json").exists())',[job]).strip()
            if ready=='True':return s
        time.sleep(c['monitoring']['refresh_s'])


def pull(c,job):
    out=Path(c['results_dir'])/Path(job).name;out.mkdir(parents=True,exist_ok=True)
    print('回传结果…',flush=True);sync(c,str(out)+'/',job+'/',True)
    manifest=json.loads((out/'files.json').read_text())
    for name,expected in manifest.items():
        p=out/name
        if not p.is_relative_to(out) or sha(p)!=expected:raise RuntimeError('Result checksum mismatch: '+name)
    receipt=dict(transfer='complete',files=len(manifest),verified_at=time.time());(out/'transfer.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('结果已校验：'+str(out),flush=True);return out


def main():
    p=argparse.ArgumentParser(description='LIVO2：主机一键运行 RK3588 数据集实验')
    p.add_argument('--config',default=str(ROOT/'project.yaml'))
    sub=p.add_subparsers(dest='command',required=True)
    for name in ['doctor','build','deploy','ssh','run','stop']:sub.add_parser(name)
    sp=sub.add_parser('status');sp.add_argument('--follow',action='store_true');sp.add_argument('--raw',action='store_true')
    sp=sub.add_parser('pull');sp.add_argument('run_id',nargs='?')
    a=p.parse_args();c=config(a.config)
    if a.command=='doctor':doctor(c)
    elif a.command=='build':build(c)
    elif a.command=='deploy':deploy(c)
    elif a.command=='ssh':
        root=workspace(c);cmd='cd '+shlex.quote(root)+'; source '+shlex.quote(root+'/current/env.sh')+'; exec bash --noprofile --norc'
        try:
            job=latest(c);active=state(c,job)
            if active.get('master_uri') and active['phase']!='finished':
                cmd='source '+shlex.quote(root+'/current/env.sh')+'; export ROS_MASTER_URI='+shlex.quote(active['master_uri'])+'; cd '+shlex.quote(job)+'; exec bash --noprofile --norc'
        except (subprocess.CalledProcessError,ValueError,KeyError):pass
        subprocess.run(['ssh','-t','-p',str(c['board']['ssh_port']),c['board']['target'],'bash -c '+shlex.quote(cmd)],check=True)
    elif a.command=='run':
        doctor(c);root,install=deploy(c);runid=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];job=Path(c['results_dir'])/runid;prepare(c,job,install,root);remotejob=root+'/runs/'+runid
        remote(c,'from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(exist_ok=False)',[remotejob]);sync(c,str(job)+'/',remotejob+'/')
        command=shlex.join(['/usr/bin/python3',install+'/runtime/agent.py','start',remotejob]);print(ssh(c,command,capture_output=True,text=True).stdout.strip())
        time.sleep(1)
        try:s=follow(c,remotejob)
        except KeyboardInterrupt:
            remote(c,'from pathlib import Path;import sys;(Path(sys.argv[1])/"stop").touch()',[remotejob]);s=follow(c,remotejob)
        if s:
            pull(c,remotejob)
            if s['algorithm']=='stopped':print('任务已停止，结果已回传',flush=True);return
            if s['algorithm']!='complete':raise RuntimeError(s.get('reason') or s['algorithm'])
        else:raise RuntimeError('Connection interrupted; job continues. Use status --follow and pull to recover.')
    elif a.command=='status':
        job=latest(c)
        if a.raw:ssh(c,'tail -n 80 '+shlex.quote(job+'/logs/livo.log'))
        elif a.follow:follow(c,job)
        else:
            s=state(c,job);print(Path(job).name+' | '+status_line(s))
            for alert in s.get('progress',{}).get('alerts',{}).values():print(f"[{alert['level']}] {alert['message']} (累计 {alert['count']})")
    elif a.command=='stop':
        job=latest(c)
        if state(c,job)['phase']!='finished':remote(c,'from pathlib import Path;import sys;(Path(sys.argv[1])/"stop").touch()',[job])
        follow(c,job);pull(c,job)
    elif a.command=='pull':pull(c,latest(c,a.run_id))


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已退出终端查看；任务状态可用 status 查询',file=sys.stderr);sys.exit(130)
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.CalledProcessError) as e:
        print('错误：'+str(e),file=sys.stderr)
        if isinstance(e,subprocess.CalledProcessError) and e.stderr:print(e.stderr[-4000:],file=sys.stderr)
        sys.exit(1)
