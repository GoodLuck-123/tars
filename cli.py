"""One entry point for local builds and managed RK3588 dataset jobs."""
import argparse
import hashlib
import ipaddress
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
    defaults=yaml.safe_load((ROOT/'project.example.yaml').read_text())
    for section in ['capture','calibration']:c.setdefault(section,defaults[section])
    c.setdefault('transfer',{}).setdefault('delete_remote_bags_after_verify',True)
    if type(c['transfer']['delete_remote_bags_after_verify']) is not bool:raise ValueError('transfer.delete_remote_bags_after_verify must be true or false')
    c['board'].setdefault('sync_time',True)
    if type(c['board']['sync_time']) is not bool:raise ValueError('board.sync_time must be true or false')
    c['topics'].setdefault('raw_image','/camera/image_raw')
    cap=c['capture']
    cap.setdefault('camera',dict(type='custom'));cap.setdefault('compression','none')
    for key,default in [('camera_info','/camera/camera_info'),('camera_metadata','/camera/metadata')]:c['topics'].setdefault(key,default)
    if cap['compression'] not in {'none','lz4'}:raise ValueError('capture.compression must be none or lz4')
    if cap['camera']['type'] not in {'custom','spinnaker'}:raise ValueError('capture.camera.type must be custom or spinnaker')
    if cap['camera']['type']=='spinnaker':
        if cap['image_transport']!='raw':raise ValueError('Spinnaker capture requires image_transport: raw')
        import math
        cam=cap['camera']
        if not math.isfinite(cam['fps']) or cam['fps']<=0:raise ValueError('capture.camera.fps must be finite and positive')
        if cam.get('exposure_auto','Continuous')=='Off' and (not math.isfinite(cam['exposure_us']) or not 0<cam['exposure_us']<=1e6/cam['fps']):raise ValueError('exposure_us 必须为正数且不超过帧周期；20Hz 时最多 50000us')
    if cap['livox'].get('ip'):cap['livox']['ip']=str(ipaddress.IPv4Address(cap['livox']['ip']))
    if cap['image_transport'] not in {'raw','compressed'}:raise ValueError('capture.image_transport must be raw or compressed')
    if not isinstance(cap['camera_command'],list) or any(not isinstance(s,str) for s in cap['camera_command']):raise ValueError('capture.camera_command must be an argument list')
    for k in ['replay_duration_s','buffer_mib','split_mib','startup_timeout_s','stall_timeout_s']:
        if cap[k]<=0:raise ValueError('capture.'+k+' must be positive')
    if cap['duration_s'] is not None and cap['duration_s']<=0:raise ValueError('capture.duration_s must be positive or null')
    if not .1<=cap['livox']['publish_hz']<=100:raise ValueError('Invalid Livox publication rate')
    for key in ['image','pcd']:
        if c['calibration'][key]:c['calibration'][key]=str((path.parent/Path(c['calibration'][key]).expanduser()).resolve())
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
    c['camera'].setdefault('output_encoding','bgr8')
    if c['camera']['output_encoding'] not in {'bgr8','mono8'}:raise ValueError('camera.output_encoding must be bgr8 or mono8')
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
    if c.get('_host'):return subprocess.run(['bash','-c',command],check=True,**kw)
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2','-p',str(c['board']['ssh_port']),c['board']['target'],command],check=True,**kw)


def remote(c,code,args=()):
    cmd='python3 - '+shlex.join(list(map(str,args)))
    return ssh(c,cmd,input=code,text=True,capture_output=True).stdout


def workspace(c):
    return remote(c,'import os,sys;p=os.path.abspath(os.path.expanduser(sys.argv[1]));assert p not in ["/",os.path.expanduser("~")],"Choose a dedicated workspace";print(p)',[c['board']['workspace']]).strip()


def sync(c,source,dest,receive=False):
    if c.get('_host'):
        # Finished local runs are immutable; share their files instead of duplicating bags.
        links=['--link-dest='+str(Path(dest).resolve())] if receive else []
        local(['rsync','-a','--partial',*links,dest if receive else source,source if receive else dest],stdout=subprocess.DEVNULL);return
    transport=shlex.join(['ssh','-p',str(c['board']['ssh_port']),'-o','BatchMode=yes','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2'])
    target=c['board']['target']+':'+dest
    local(['rsync','-a','--partial','--protect-args','--info=progress2','-e',transport,target if receive else source,source if receive else target],stdout=subprocess.DEVNULL)


def image_input(c,found):
    for key,typ,transport in [('compressed_image','sensor_msgs/msg/CompressedImage','compressed'),('raw_image','sensor_msgs/msg/Image','raw')]:
        if found.get(c['topics'][key])==typ:
            if c['topics'][key]==c['topics']['image']:raise ValueError('Image input and rectified output topics must differ')
            return key,transport
    raise RuntimeError('Missing image input; check topics.raw_image / compressed_image')


def doctor(c,sensors=False):
    print('检查主机与板端…',flush=True)
    info=json.loads(remote(c,'import json,platform,shutil,os;print(json.dumps(dict(architecture=platform.machine(),python=platform.python_version(),free_gib=shutil.disk_usage(os.path.expanduser("~")).free/2**30,rsync=shutil.which("rsync"))))'))
    print(json.dumps(info))
    if info['architecture']!=('x86_64' if c.get('_host') else 'aarch64') or not info['rsync']:raise RuntimeError('Wrong target architecture or missing rsync')
    if info['free_gib']<c['run']['min_free_gib']:raise RuntimeError('Board disk space below configured reserve')
    if sensors:
        root=workspace(c)
        result=json.loads(remote(c,(ROOT/'runtime/sensor_doctor.py').read_text(),[root,json.dumps(c['capture']['livox']),str(ROOT/'.build/host/install') if c.get('_host') else '',str(time.time()),json.dumps(c['capture']['camera'])]))
        if result['free_gib']<c['run']['min_free_gib']:result['problems'].append('工作目录空间低于保留阈值')
        result['ok']=not result['problems']
        cache=ROOT/'.cache';cache.mkdir(exist_ok=True);(cache/('doctor-host.json' if c.get('_host') else 'doctor-board.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print('目标：'+c['board']['target']+' | 运行包：'+result['release'],flush=True)
        print('目标端与主机时钟差（粗测）：'+str(result['host_offset_estimate_s'])+' 秒',flush=True)
        print('SDK/驱动及私有 ROS：'+('通过构建记录与运行库检查' if result.get('environment_ready') else '未就绪'),flush=True)
        net=result['network'];print('雷达网络：'+str(net.get('source_ip','?'))+' ('+net.get('interface','?')+') → '+str(net['lidar_ip']),flush=True)
        if net.get('device'):print('AVIA：'+net['device']['broadcast_code']+' | '+net.get('verification',''),flush=True)
        if result.get('task'):print('当前任务：'+result['task']['run_id']+' | '+result['task']['phase']+' | '+json.dumps(result['task'].get('streams',{}),ensure_ascii=False),flush=True)
        print('相机：'+json.dumps(result.get('camera',dict(state='自定义驱动或未接入')),ensure_ascii=False),flush=True)
        if result['problems']:raise RuntimeError('；'.join(result['problems']))
        if net.get('verification')=='discovery_only':
            command='./livo record --background' if c['capture']['camera']['type']=='spinnaker' or c['capture']['camera_command'] else './livo lidar --background'
            print('设备发现通过；实际收数及录包请用 '+command+' 验证',flush=True)
        return
    p=Path(c['dataset']['path'])
    if not p.is_file():raise RuntimeError('Dataset not found: '+str(p))
    from rosbags.rosbag1 import Reader
    with Reader(p) as r:
        if c['dataset']['start_s']>=r.duration/1e9:raise ValueError('Dataset start is past EOF')
        found={x.topic:x.msgtype for x in r.connections}
        for key,typ in [('lidar','livox_ros_driver/msg/CustomMsg'),('imu','sensor_msgs/msg/Imu')]:
            if found.get(c['topics'][key])!=typ:raise RuntimeError(f'Invalid or missing {key} topic/type')
        image_input(c,found)
        print(f'数据集：{p.name}，{r.duration/1e9:.2f} 秒，{r.message_count} 条消息',flush=True)


def build(c):
    if c.get('_host'):
        from native import build as native_build
        print('构建主机 LIVO2 / 标定工具；日志 .cache/host-build.log',flush=True);native_build(c['build']['jobs']);return
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
    if c.get('_host'):
        from native import ensure
        install=ensure(c['build']['jobs']);root=workspace(c)
        remote(c,'from pathlib import Path;import sys;p=Path(sys.argv[1]);[(p/d).mkdir(parents=True,exist_ok=True) for d in ["runs","data"]]',[root]);return root,str(install)
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
    if c['capture']['camera']['type']=='spinnaker':ssh(c,'sudo -n bash '+shlex.quote(dest+'/runtime/setup_camera_usb.sh'))
    print('运行包已就绪：'+release['id'],flush=True);return root,dest


def ensure_idle(c,root):
    code="""import fcntl,json,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]);busy=None;synced=False;problem=None
with (root/'run.lock').open('a') as guard:
 try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:busy='相机实时预览；用 ./livo --host preview --stop 关闭' if (root/'preview.json').exists() else Path(json.loads((root/'latest.json').read_text())['job']).name
 if not busy and sys.argv[3]=='True' and abs(time.time()-float(sys.argv[2]))>5:
  result=subprocess.run(['sudo','-n','date','-u','-s','@'+sys.argv[2]],capture_output=True,text=True)
  if result.returncode:problem='板端时钟错误且自动校时失败：'+result.stderr.strip()
  else:synced=True
print(json.dumps(dict(busy=busy,synced=synced,problem=problem)))
"""
    result=json.loads(remote(c,code,[root,time.time(),not c.get('_host') and c['board'].get('sync_time',True)]))
    if result['busy']:raise RuntimeError('已有任务运行：'+result['busy']+'；请先用 status 查看，或 stop 停止')
    if result['problem']:raise RuntimeError(result['problem'])
    if result['synced']:print('板端时钟已按主机校正；仅空闲时校时，录制期间不调整',flush=True)


def wait_capture(c,job):
    deadline=time.monotonic()+c['capture']['startup_timeout_s']+30
    while time.monotonic()<deadline:
        try:s=state(c,job)
        except subprocess.CalledProcessError:time.sleep(.5);continue
        if s['phase']=='recording':
            print(Path(job).name+' | '+status_line(s),flush=True)
            print('传感器收数已就绪；任务独立运行，主机断网不影响录制。用 status 查看，stop 结束并回传。',flush=True);return
        if s['phase']=='finished':
            follow(c,job);pull(c,job);raise RuntimeError(s.get('reason') or '采集未进入 recording')
        time.sleep(.5)
    raise RuntimeError('等待采集就绪超时；任务可能仍在启动，请用 status 查看，不要重复启动')


def prepare(c,job,install,root):
    from rosbags.rosbag1 import Reader
    p=Path(c['dataset']['path']);ident=sha(p);bag=root+'/data/'+ident+'.bag'
    present=remote(c,'from pathlib import Path;import sys;print(Path(sys.argv[1]).stat().st_size if Path(sys.argv[1]).exists() else -1)',[bag]).strip()
    if int(present)!=p.stat().st_size:print('上传数据集…',flush=True);sync(c,str(p),bag)
    actual=remote(c,'import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],"rb");[(h.update(b)) for b in iter(lambda:f.read(1048576),b"")];print(h.hexdigest())',[bag]).strip()
    if actual!=ident:raise RuntimeError('Remote dataset hash mismatch')
    with Reader(p) as r:
        image_key,image_transport=image_input(c,{x.topic:x.msgtype for x in r.connections})
        start=r.start_time/1e9+c['dataset']['start_s'];end=min(r.end_time/1e9,start+c['dataset']['duration_s']) if c['dataset']['duration_s'] is not None else r.end_time/1e9
        input_info=dict(path=str(p),sha256=ident,start_s=start,end_s=end,topics=[dict(topic=x.topic,type=x.msgtype,count=x.msgcount) for x in r.connections])
    cfg=task_config(c,root,install,'dataset');cfg.update(bag=bag,rate=c['dataset']['rate'],start_s=c['dataset']['start_s'],duration_s=c['dataset']['duration_s'],expected_end_s=end,tail_tolerance_s=c['dataset']['tail_tolerance_s'],image_input=image_key,image_transport=image_transport)
    upstream=yaml.safe_load((ROOT/'third_party/FAST-LIVO2/config/avia.yaml').read_text());upstream['common'].update(img_topic=c['topics']['image'],lid_topic=c['topics']['lidar'],imu_topic=c['topics']['imu'])
    upstream['runtime']=dict(opencv_threads=c['runtime']['opencv_threads'])
    upstream['time_offset'].update(img_time_offset=0.0,imu_time_offset=0.0)
    e=c['extrinsics'];upstream['extrin_calib']=dict(extrinsic_T=e['t_imu_lidar'],extrinsic_R=e['R_imu_lidar'],Rcl=e['R_camera_lidar'],Pcl=e['t_camera_lidar'])
    upstream['evo']=dict(seq_name='trajectory',pose_output_en=True);upstream['pcd_save'].update(pcd_save_en=True,interval=c['run']['map_chunk_frames']);upstream['image_save']['img_save_en']=False
    for section,values in c['algorithm']['overrides'].items():upstream.setdefault(section,{}).update(values)
    fx,fy,cx,cy=c['camera']['intrinsics'];w,h=c['camera']['width'],c['camera']['height'];scale=c['camera']['scale'];ow,oh=int(w*scale),int(h*scale)
    if min(ow,oh)<1:raise ValueError('Scaled camera dimensions must be positive')
    upstream['decoder']=dict(K=[fx,0,cx,0,fy,cy,0,0,1],D=c['camera']['distortion'],width=w,height=h,output_width=ow,output_height=oh,input_transport=image_transport,output_encoding=c['camera']['output_encoding'])
    cam=dict(cam_model='Pinhole',cam_width=ow,cam_height=oh,scale=1.0,cam_fx=fx*ow/w,cam_fy=fy*oh/h,cam_cx=cx*ow/w,cam_cy=cy*oh/h,cam_d0=0.0,cam_d1=0.0,cam_d2=0.0,cam_d3=0.0)
    job.mkdir(parents=True)
    for name,value in [('config.json',cfg),('input.json',input_info)]: (job/name).write_text(json.dumps(value,indent=2)+'\n')
    for name,value in [('algorithm.yaml',upstream),('camera.yaml',cam),('config.resolved.yaml',c)]: (job/name).write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False))
    return cfg


def task_config(c,root,install,mode):
    rt=dict(c['runtime'])
    if c.get('_host') or mode!='dataset':rt.update(livo_cpus=[],cpu_governor=None)
    return dict(mode=mode,workspace=root,install=install,topics=c['topics'],monitoring=c['monitoring'],runtime=rt,**{k:c['run'][k] for k in ['timeout_s','shutdown_timeout_s','drain_timeout_s','min_free_gib']})


def prepare_capture(c,job,install,root,replay,lidar_only=False):
    cap=c['capture'];cfg=prepare(c,job,install,root) if replay else task_config(c,root,install,'capture')
    cfg.update(mode='capture_replay' if replay else 'capture_lidar' if lidar_only else 'capture',capture=cap,calibration_verified=cap['calibration_verified'])
    cfg['runtime'].update(livo_cpus=[],cpu_governor=None)
    if not replay and not lidar_only and cap['camera']['type']=='spinnaker':
        cfg['capture']=dict(cap)
        cfg['capture']['camera_command']=[install+'/bin/livo_flir_camera','image_raw:='+c['topics']['raw_image'],'metadata:='+c['topics']['camera_metadata'],'camera_info:='+c['topics']['camera_info']]
        cfg['capture']['extra_topics']=list(dict.fromkeys([*cap['extra_topics'],c['topics']['camera_metadata'],c['topics']['camera_info']]))
    image_key=cfg['image_input'] if replay else 'compressed_image' if cap['image_transport']=='compressed' else 'raw_image'
    cfg['capture_topics']={k:dict(topic=c['topics'][key],type=typ) for k,key,typ in [('lidar','lidar','livox_ros_driver/CustomMsg'),('imu','imu','sensor_msgs/Imu'),('image',image_key,'sensor_msgs/CompressedImage' if image_key=='compressed_image' else 'sensor_msgs/Image')]}
    cfg['preview_gamma']=c.get('viewer',{}).get('bayer_gamma',1.0)
    if lidar_only:cfg['capture_topics'].pop('image')
    job.mkdir(parents=True,exist_ok=True)
    if not replay and not lidar_only and cap['camera']['type']=='spinnaker':
        (job/'camera-driver.yaml').write_text(yaml.safe_dump({k:v for k,v in cap['camera'].items() if k!='type'}))
    if not replay:
        codes=cap['livox']['broadcast_codes']
        if cap['livox'].get('ip'):
            info=json.loads(remote(c,(ROOT/'runtime/livox_discovery.py').read_text(),[cap['livox']['ip']]))
            if codes and info['broadcast_code'] not in codes:raise RuntimeError('Livox IP and configured broadcast codes do not match')
            codes=[info['broadcast_code']];(job/'device.json').write_text(json.dumps(info,indent=2)+'\n');print('发现 AVIA：'+info['ip']+' / '+info['broadcast_code'],flush=True)
        raw=dict(lidar_config=[dict(broadcast_code=s,enable_connect=True,return_mode=0,coordinate=0,imu_rate=1,extrinsic_parameter_source=0,enable_high_sensitivity=False) for s in codes],timesync_config=dict(enable_timesync=False))
        (job/'livox.json').write_text(json.dumps(raw,indent=2)+'\n')
        settings=dict(xfer_format=1,multi_topic=0,data_src=0,publish_freq=cap['livox']['publish_hz'],output_data_type=0,user_config_path=root+'/runs/'+job.name+'/livox.json',frame_id='livox_frame',enable_lidar_bag=False,enable_imu_bag=False)
        (job/'livox.yaml').write_text(yaml.safe_dump(settings));(job/'config.resolved.yaml').write_text(yaml.safe_dump(c,allow_unicode=True))
    (job/'config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    return cfg


def prepare_calibration(c,job,install,root):
    import re
    cal=c['calibration'];cfg=task_config(c,root,install,'calibrate');cfg['timeout_s']=cal['timeout_s'];job.mkdir(parents=True)
    fx,fy,cx,cy=c['camera']['intrinsics'];dist=c['camera']['distortion']
    if len(dist) not in {4,5}:raise ValueError('livox_camera_calib requires the pinhole 4/5 coefficient distortion model')
    settings=(ROOT/'third_party/livox_camera_calib/config/config_outdoor.yaml').read_text()
    r=c['extrinsics']['R_camera_lidar'];t=c['extrinsics']['t_camera_lidar'];matrix=[v for i in range(3) for v in [*r[i*3:i*3+3],t[i]]]+[0,0,0,1]
    settings=re.sub(r'data:\s*\[.*?\]','data: '+str(matrix),settings,count=1,flags=re.S)
    for k,v in cal['overrides'].items():
        settings,n=re.subn(r'^'+re.escape(k)+r':.*$',k+': '+str(v),settings,flags=re.M)
        if n!=1:raise ValueError('Unknown calibration setting: '+k)
    (job/'calibration-settings.yaml').write_text(settings)
    params=dict(common=dict(image_file=str(Path(cal['image']).expanduser().resolve()),pcd_file=str(Path(cal['pcd']).expanduser().resolve()),result_file=str(job/'extrinsic.txt')),camera=dict(camera_matrix=[fx,0,cx,0,fy,cy,0,0,1],dist_coeffs=dist+[0]*(5-len(dist))),calib=dict(use_rough_calib=cal['use_rough_calib'],calib_config_file=str(job/'calibration-settings.yaml')))
    # Host job paths are distinct from the collected results directory.
    for section,key in [('common','result_file'),('calib','calib_config_file')]:params[section][key]=root+'/runs/'+job.name+'/'+Path(params[section][key]).name
    (job/'calibration.yaml').write_text(yaml.safe_dump(params));(job/'config.json').write_text(json.dumps(cfg,indent=2)+'\n');(job/'config.resolved.yaml').write_text(yaml.safe_dump(c,allow_unicode=True))
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
    if s.get('capture'):
        streams=s['capture'].get('streams',{})
        text=f"{s['phase']} | {s.get('elapsed_s',0):.1f}s | "+' | '.join(f"{k} {v['messages']} 条 {v['hz']:.1f}Hz" for k,v in streams.items())
    else:text=f"{s['phase']} | {s.get('elapsed_s',0):.1f}s | 处理 {m.get('processed',0)} | 视觉更新 {m.get('visual_updates',0)}"
    text+=f" | WARN {m.get('warnings',0)} ERROR {m.get('errors',0)}"
    if s['phase']=='finished':text+=' | 结果 '+s.get('algorithm','unknown')+(' | '+s['reason'] if s.get('reason') else '')
    elif s.get('heartbeat') and time.time()-s['heartbeat']>10:text+=' | 任务心跳过期，请检查板端日志'
    return text


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
    if state(c,job)['phase']!='finished':raise RuntimeError('任务仍在运行；请先 ./livo stop 正常结束并回传，避免复制未完成的 bag')
    ready=remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"files.json").exists())',[job]).strip()
    if ready!='True' and follow(c,job) is None:raise RuntimeError('等待结果清单时连接中断；请重新 pull')
    out=Path(c['results_dir'])/Path(job).name;out.mkdir(parents=True,exist_ok=True)
    print('回传结果…',flush=True);sync(c,str(out)+'/',job+'/',True)
    manifest=json.loads((out/'files.json').read_text())
    for name,expected in manifest.items():
        p=out/name
        if not p.resolve().is_relative_to(out.resolve()) or sha(p)!=expected:raise RuntimeError('Result checksum mismatch: '+name)
        if name.endswith(('.bag','.bag.active')):
            with p.open('rb') as stream:os.fsync(stream.fileno())
    # Persist directory entries too before authorizing removal of the board copy.
    for directory in {out,*(p.parent for p in out.rglob('*') if p.is_file())}:
        fd=os.open(directory,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    receipt=dict(transfer='complete',files=len(manifest),verified_at=time.time());(out/'transfer.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if not c.get('_host') and c['transfer']['delete_remote_bags_after_verify']:
        try:
            cleaned=json.loads(remote(c,(ROOT/'runtime/transfer_cleanup.py').read_text(),[workspace(c),job,sha(out/'files.json')]))
        except (OSError,ValueError,subprocess.CalledProcessError) as e:
            raise RuntimeError('主机结果已校验；板端录包清理未确认，请重新 ./livo pull '+Path(job).name) from e
        receipt['remote_bag_cleanup']=cleaned
        (out/'transfer.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(f"板端录包已清理：{cleaned['deleted_bags']} 个，释放 {cleaned['freed_bytes']/2**30:.2f} GiB；日志与任务记录保留",flush=True)
    print('结果已校验：'+str(out),flush=True);return out


def main():
    p=argparse.ArgumentParser(prog='./livo',description='LIVO：板端采集 / 主机 LIVO2 与免标定板标定',epilog='雷达：./livo lidar --background → ./livo stop → ./livo view。所有设备地址和用户参数统一编辑 project.yaml。')
    p.add_argument('--config',default=str(ROOT/'project.yaml'),help='选择完整配置文件，默认 project.yaml')
    p.add_argument('--host',action='store_true',help='在主机私有环境运行；默认连接板端')
    sub=p.add_subparsers(dest='command',required=True)
    for name,help_text in [('build','仅构建 SDK/驱动/算法；--host 构建主机工具'),('deploy','仅部署运行环境'),('ssh','进入私有 ROS 环境和当前任务'),('run','运行配置中的数据集并回传；--host 在电脑处理'),('stop','正常停止当前任务并回传')]:sub.add_parser(name,help=help_text)
    sp=sub.add_parser('doctor',help='检查环境与数据；--sensors 只检查采集端');sp.add_argument('--sensors',action='store_true',help='检查采集端环境与设备节点，不依赖示例数据集')
    sp=sub.add_parser('record',help='录制传感器数据并回传');sp.add_argument('--background',action='store_true',help='确认收数后返回，板端独立录制，使用 stop 结束并回传');sp.add_argument('--replay',action='store_true',help='无硬件演练，使用配置中的现有 bag')
    sp=sub.add_parser('lidar',help='仅启动 AVIA 点云/IMU 并录包，不需要相机；本机加 --host');sp.add_argument('--background',action='store_true',help='确认点云/IMU 收数后返回，主机断线仍录制，用 stop 结束并回传')
    sp=sub.add_parser('preview',help='实时查看主机相机：空闲时不录包，录制中复用图像流');sp.add_argument('--no-open',action='store_true',help='只打印本机预览地址')
    sp.add_argument('--stop',action='store_true',help='关闭本工程启动的实时预览，不停止录包')
    sub.add_parser('calibrate',help='在主机运行免标定板外参标定，输出候选结果')
    sp=sub.add_parser('view',help='离线播放录包：3D 点云 + 同时间轴相机；默认最近一次录制');sp.add_argument('bag',nargs='?',help='bag 文件或包含分包的录制目录');sp.add_argument('--no-open',action='store_true',help='只启动本地服务，不自动打开浏览器')
    sp=sub.add_parser('status',help='查看摘要/告警；--follow 持续查看，--raw 看原始日志');sp.add_argument('--follow',action='store_true');sp.add_argument('--raw',action='store_true')
    sp=sub.add_parser('pull',help='补收最近一次或指定任务，校验全部文件');sp.add_argument('run_id',nargs='?')
    if len(sys.argv)==1:p.print_help();return
    a=p.parse_args();c=config(a.config)
    if a.host or a.command=='calibrate':c['_host']=True;c['board']['workspace']=str(ROOT/'.cache/host-workspace');c['board']['target']='local'
    if a.command=='record' and not a.replay and not c['capture']['camera_command'] and c['capture']['camera']['type']!='spinnaker':raise RuntimeError('相机尚未配置；只录 AVIA 请用 ./livo lidar。接入相机后在 project.yaml 设置 capture.camera_command。')
    if a.command=='calibrate':
        for key in ['image','pcd']:
            if not c['calibration'][key] or not Path(c['calibration'][key]).expanduser().is_file():raise RuntimeError('请在 project.yaml 设置 calibration.'+key+' 输入文件')
    if a.command=='view':
        from viewer.server import serve
        serve(c,a.bag,not a.no_open)
    elif a.command=='preview':
        if not c.get('_host'):raise RuntimeError('当前实时预览请用 ./livo --host preview；板端数据使用 stop 回传后 view')
        if a.stop:
            import signal
            record_path=ROOT/'.cache/camera-preview.json'
            if not record_path.exists():print('实时预览已关闭');return
            record=json.loads(record_path.read_text());pid=record['pid']
            if not Path('/proc/'+str(pid)+'/cmdline').exists():print('实时预览已关闭');return
            if str(ROOT).encode() not in Path('/proc/'+str(pid)+'/cmdline').read_bytes() or b'camera_preview.py' not in Path('/proc/'+str(pid)+'/cmdline').read_bytes():raise RuntimeError('预览进程身份不匹配')
            os.kill(pid,signal.SIGINT);deadline=time.monotonic()+25
            while Path('/proc/'+str(pid)).exists() and time.monotonic()<deadline:time.sleep(.1)
            if Path('/proc/'+str(pid)).exists():raise RuntimeError('预览仍在清理设备；请稍后重试')
            print('实时预览已关闭；录包不受影响');return
        from native import ensure
        install=str(ensure(c['build']['jobs']));root=Path(workspace(c));root.mkdir(parents=True,exist_ok=True)
        try:job=latest(c);s=state(c,job)
        except (OSError,ValueError,subprocess.CalledProcessError):s=dict(phase='finished')
        standalone=s['phase']=='finished'
        if standalone:
            if c['capture']['camera']['type']!='spinnaker':raise RuntimeError('独立预览需要 capture.camera.type: spinnaker')
            ensure_idle(c,str(root))
            job=ROOT/'.cache/camera-preview';job.mkdir(exist_ok=True)
            cfg=dict(install=install,workspace=str(root),capture_topics=dict(image=dict(topic=c['topics']['raw_image'])),preview_gamma=c.get('viewer',{}).get('bayer_gamma',1.0))
            (job/'config.json').write_text(json.dumps(cfg));(job/'camera-driver.yaml').write_text(yaml.safe_dump({k:v for k,v in c['capture']['camera'].items() if k!='type'}))
        else:cfg=json.loads((Path(job)/'config.json').read_text())
        if 'image' not in cfg.get('capture_topics',{}):raise RuntimeError('当前任务没有相机图像')
        cache=ROOT/'.cache/camera-preview.json';args=['python3',cfg['install']+'/runtime/camera_preview.py',str(job),str(cache)]+(['--standalone'] if standalone else [])+(['--no-open'] if a.no_open else [])
        command='source '+shlex.quote(cfg['install']+'/env.sh')+'; '+('' if standalone else 'export ROS_MASTER_URI='+shlex.quote(s['master_uri'])+' ROS_IP=127.0.0.1; ')+'exec '+shlex.join(args)
        ssh(c,command)
    elif a.command=='doctor':doctor(c,a.sensors)
    elif a.command=='build':build(c)
    elif a.command=='deploy':
        root=workspace(c)
        remote(c,'from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(parents=True,exist_ok=True)',[root]);ensure_idle(c,root)
        deploy(c)
    elif a.command=='ssh':
        root=workspace(c)
        if c.get('_host'):
            from native import ensure
            prefix=str(ensure(c['build']['jobs']))
        else:prefix=root+'/current'
        cmd='cd '+shlex.quote(root)+'; source '+shlex.quote(prefix+'/env.sh')+'; exec bash --noprofile --norc'
        try:
            job=latest(c);active=state(c,job)
            if active.get('master_uri') and active['phase']!='finished':
                cmd='source '+shlex.quote(prefix+'/env.sh')+'; export ROS_MASTER_URI='+shlex.quote(active['master_uri'])+'; cd '+shlex.quote(job)+'; exec bash --noprofile --norc'
        except (subprocess.CalledProcessError,ValueError,KeyError):pass
        if c.get('_host'):local(['bash','-c',cmd])
        else:subprocess.run(['ssh','-t','-p',str(c['board']['ssh_port']),c['board']['target'],'bash -c '+shlex.quote(cmd)],check=True)
    elif a.command in {'run','record','calibrate','lidar'}:
        if a.command=='run' or (a.command=='record' and a.replay):doctor(c)
        root=workspace(c)
        remote(c,'from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(parents=True,exist_ok=True)',[root]);ensure_idle(c,root)
        root,install=deploy(c);runid=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];job=Path(c['results_dir'])/runid
        if a.command in {'record','lidar'}:prepare_capture(c,job,install,root,a.replay if a.command=='record' else False,a.command=='lidar')
        elif a.command=='calibrate':prepare_calibration(c,job,install,root)
        else:prepare(c,job,install,root)
        remotejob=root+'/runs/'+runid
        remote(c,'from pathlib import Path;import sys;Path(sys.argv[1]).mkdir(exist_ok=False)',[remotejob]);sync(c,str(job)+'/',remotejob+'/')
        command=shlex.join(['/usr/bin/python3',install+'/runtime/agent.py','start',remotejob]);print(ssh(c,command,capture_output=True,text=True).stdout.strip())
        if a.command in {'lidar','record'} and a.background:
            wait_capture(c,remotejob);return
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
        if a.raw:
            cfg=json.loads(remote(c,'from pathlib import Path;import sys;print((Path(sys.argv[1])/"config.json").read_text())',[job]));mode=cfg.get('mode','dataset')
            names=['livox','camera','record'] if mode.startswith('capture') and mode!='capture_replay' else ['record'] if mode=='capture_replay' else ['calibration'] if mode=='calibrate' else ['livo']
            logs=json.loads(remote(c,'import json,sys;from pathlib import Path;p=Path(sys.argv[1])/"logs";print(json.dumps([str(p/(n+".log")) for n in sys.argv[2:] if (p/(n+".log")).exists()]))',[job,*names]))
            if logs:ssh(c,'tail -n 40 '+shlex.join(logs))
            else:print('日志尚未生成')
        elif a.follow:follow(c,job)
        else:
            s=state(c,job);print(Path(job).name+' | '+status_line(s))
            for alert in s.get('progress',{}).get('alerts',{}).values():print(f"[{alert['level']}] {alert['message']} (累计 {alert['count']})")
    elif a.command=='stop':
        job=latest(c)
        if state(c,job)['phase']!='finished':remote(c,'from pathlib import Path;import sys;(Path(sys.argv[1])/"stop").touch()',[job])
        s=follow(c,job)
        if s is None:raise RuntimeError('连接中断；板端仍会执行停止，请重连后再次 stop 或 pull')
        pull(c,job)
        if s['algorithm']=='failed':raise RuntimeError(s.get('reason') or '任务失败，已保留并回传证据')
    elif a.command=='pull':pull(c,latest(c,a.run_id))


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已退出终端查看；任务状态可用 status 查询',file=sys.stderr);sys.exit(130)
    except (RuntimeError,ValueError,KeyError,OSError,subprocess.CalledProcessError) as e:
        print('错误：'+str(e),file=sys.stderr)
        if isinstance(e,subprocess.CalledProcessError) and e.stderr:print(e.stderr[-4000:],file=sys.stderr)
        sys.exit(1)
