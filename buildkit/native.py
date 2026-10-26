"""Private native ROS environment and builds; never source another project."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
from fetch import ROOT,sha,obtain
ENV=ROOT/'.cache/host-env'
INSTALL=ROOT/'.build/host/install'


def environment():
    return {**os.environ,'MAMBA_ROOT_PREFIX':str(ROOT/'.cache/mamba'),'XDG_CACHE_HOME':str(ROOT/'.cache/xdg'),'PIP_CACHE_DIR':str(ROOT/'.cache/pip')}


def bootstrap():
    tool=ROOT/'.cache/tools/micromamba';meta=json.loads((ROOT/'buildkit/host-tool.json').read_text())
    if not tool.exists():
        archive=obtain(meta);tool.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive) as t:tool.write_bytes(t.extractfile('bin/micromamba').read())
        tool.chmod(0o755)
    if sha(tool)!=meta['binary_sha256']:raise RuntimeError('Native environment tool checksum mismatch')
    lock=ROOT/'buildkit/locks/host-linux-64.txt'
    expected={s for s in lock.read_text().splitlines() if s.startswith('https://')}
    actual=set()
    for p in (ENV/'conda-meta').glob('*.json'):
        d=json.loads(p.read_text());actual.add(d.get('url','')+'#'+d.get('md5',''))
    if actual!=expected:
        with (ROOT/'.cache/host-environment.log').open('w') as log:
            subprocess.run([tool,'create','-p',ENV,'--file',lock,'-y','--no-rc'],env=environment(),stdout=log,stderr=subprocess.STDOUT,check=True)
    return tool


def build(jobs=4):
    tool=bootstrap();log=ROOT/'.cache/host-build.log'
    with log.open('w') as f:
        r=subprocess.run([tool,'run','-p',ENV,'python',ROOT/'buildkit/build.py',str(jobs),'host'],env=environment(),stdout=f,stderr=subprocess.STDOUT)
    if r.returncode:raise RuntimeError('Native build failed; inspect '+str(log))
    INSTALL.mkdir(parents=True,exist_ok=True);(INSTALL/'bin').mkdir(exist_ok=True)
    targets={'livox_ros_driver_node':'stage/lib/livox_ros_driver/livox_ros_driver_node','fastlivo_mapping':'stage/lib/fast_livo/fastlivo_mapping','livo_image_decode':'image-decode/livo_image_decode','lidar_camera_calib':'stage/lib/livox_camera_calib/lidar_camera_calib','lidar_camera_multi_calib':'stage/lib/livox_camera_calib/lidar_camera_multi_calib','bag_to_pcd':'stage/lib/livox_camera_calib/bag_to_pcd'}
    for name,p in targets.items():
        if not (INSTALL.parent/p).is_file():raise RuntimeError('Missing native build output: '+p)
        link=INSTALL/'bin'/name;link.unlink(missing_ok=True);link.symlink_to(INSTALL.parent/p)
    shutil.copytree(ROOT/'runtime',INSTALL/'runtime',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    stage=INSTALL.parent/'stage';python_dirs=list((stage/'lib').glob('python*/site-packages'))+list((stage/'lib').glob('python*/dist-packages'))
    exports=dict(PATH=str(INSTALL/'bin')+':'+str(ENV/'bin')+':/usr/bin:/bin',LD_LIBRARY_PATH=str(stage/'lib')+':'+str(ENV/'lib'),PYTHONPATH=':'.join(map(str,python_dirs))+':'+str(ENV/'lib/python3.11/site-packages'),CMAKE_PREFIX_PATH=str(stage)+':'+str(ENV),ROS_PACKAGE_PATH=str(stage/'share')+':'+str(ENV/'share'),ROS_ROOT=str(ENV/'share/ros'),ROS_ETC_DIR=str(ENV/'etc/ros'),ROS_DISTRO='noetic',ROS_VERSION='1',ROS_PYTHON_VERSION='3',PYTHONNOUSERSITE='1')
    (INSTALL/'env.sh').write_text('unset PYTHONHOME PYTHONPATH ROS_PACKAGE_PATH AMENT_PREFIX_PATH CMAKE_PREFIX_PATH ROS_HOSTNAME\n'+''.join('export '+k+'='+shlex.quote(v)+'\n' for k,v in exports.items()))
    from bundle import input_fingerprint
    (INSTALL/'build.json').write_text(json.dumps(dict(input_fingerprint=input_fingerprint(),host_lock_sha256=sha(ROOT/'buildkit/locks/host-linux-64.txt')))+'\n')


def ensure(jobs=4):
    from bundle import input_fingerprint
    meta=INSTALL/'build.json'
    if not meta.exists() or json.loads(meta.read_text())['input_fingerprint']!=input_fingerprint():
        print('准备主机 ROS / LIVO2 / 标定工具；日志 .cache/host-build.log',flush=True);build(jobs)
    return INSTALL
