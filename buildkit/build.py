"""Build visible, pinned third_party sources with small, tracked portability patches."""
import json,os,shutil,subprocess,sys
from pathlib import Path
from fetch import ROOT,CACHE,packages,sources,relocate,sha
BUILD=ROOT/'.build';SDK=CACHE/'sysroot';TOOL=CACHE/'toolchain';STAGE=BUILD/'stage'


def run(args,**kw):
    print('+',' '.join(map(str,args)),flush=True);subprocess.run(list(map(str,args)),check=True,**kw)


def write(p,text):
    p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists() or p.read_text()!=text:p.write_text(text)


def apply_patch(name,dest):
    patch=ROOT/'patches'/(name+'.patch');marker=BUILD/('patch-'+name+'.sha256')
    if marker.exists() and marker.read_text().strip()==sha(patch):return
    common=['patch','--batch','-p1','-d',str(dest),'-i',str(patch)]
    forward=subprocess.run(common+['--dry-run','--forward'],capture_output=True)
    if forward.returncode==0:run(common+['--forward'])
    elif subprocess.run(common+['--dry-run','--reverse'],capture_output=True).returncode:raise RuntimeError('Patch conflicts with source edits: '+str(patch))
    marker.write_text(sha(patch)+'\n')


def main(jobs=4,target='board'):
    global BUILD,SDK,STAGE
    native=target=='host'
    if native:BUILD=ROOT/'.build/host';SDK=ROOT/'.cache/host-env';STAGE=BUILD/'stage'
    else:
        for name,d in [('toolchain',TOOL),('ros1-sdk',SDK),('pcl',SDK)]:packages(name,d)
    sources()
    if not native:relocate(SDK)
    BUILD.mkdir(exist_ok=True,parents=True);STAGE.mkdir(exist_ok=True)
    records=json.loads((ROOT/'dependencies.lock').read_text())['sources'];src={r['name']:ROOT/r['directory'] for r in records}
    for name in ['fast-livo2','sophus','vikit','livox-driver','livox-sdk']:apply_patch(name,src[name])
    if native:apply_patch('camera-calib',src['camera-calib'])
    changes={name:subprocess.check_output(['git','-C',src[name],'diff'],text=True) for name in src}
    write(BUILD/'patches.json',json.dumps(changes,indent=2)+'\n')
    hostlibs=CACHE/'host-libs';hostlibs.mkdir(exist_ok=True)
    for pattern in ['libisl.so.22','libgmp.so.10','libmpc.so.3','libmpfr.so.6','libbfd*-arm64.so*','libopcodes*-arm64.so*','libctf*-arm64.so*']:
        for p in (TOOL/'usr/lib/x86_64-linux-gnu').glob(pattern):
            d=hostlibs/p.name
            if not d.exists():d.symlink_to(p)
    if not native:
        os.environ.update(LIVO_SYSROOT=str(SDK),LIVO_TOOLCHAIN=str(TOOL),LIVO_STAGE=str(STAGE),LD_LIBRARY_PATH=str(hostlibs),PYTHONPATH=str(SDK/'opt/ros/noetic/lib/python3/dist-packages')+':'+str(SDK/'usr/lib/python3/dist-packages'),PKG_CONFIG_SYSROOT_DIR=str(SDK),PKG_CONFIG_LIBDIR=str(SDK/'usr/lib/aarch64-linux-gnu/pkgconfig'))
        os.environ.pop('CONDA_PREFIX',None)
    common=['-DCMAKE_BUILD_TYPE=Release','-DCATKIN_ENABLE_TESTING=OFF','-DPYTHON_EXECUTABLE='+str(SDK/'bin/python' if native else '/usr/bin/python3'),'-DCATKIN_DEVEL_PREFIX='+str(STAGE),'-DCMAKE_INSTALL_PREFIX='+str(STAGE),'-DCMAKE_PREFIX_PATH='+str(STAGE)+';'+str(SDK if native else SDK/'opt/ros/noetic')+';'+str(SDK/'usr'),'-DEigen3_DIR='+str(SDK/('share/eigen3/cmake' if native else 'usr/share/eigen3/cmake')),'-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DPKG_CONFIG_EXECUTABLE='+str(SDK/'bin/pkg-config' if native else '/usr/bin/pkg-config'),'-DSophus_DIR='+str(BUILD/'sophus')]
    if not native:common+=['-DCMAKE_TOOLCHAIN_FILE='+str(ROOT/'buildkit/aarch64.cmake')]
    else:common+=['-DCMAKE_PROJECT_INCLUDE='+str(ROOT/'buildkit/native-compat.cmake')]
    def build(name,source,target=None,install=False):
        path=BUILD/name;run(['cmake','-S',source,'-B',path,*common])
        run(['cmake','--build',path,'-j',jobs]+(['--target',target] if target else []))
        if install:run(['cmake','--install',path])
    build('sophus',src['sophus'],'Sophus',True)
    build('livox-sdk',src['livox-sdk'],'livox_sdk_static',True)
    build('livox-driver',src['livox-driver']/'livox_ros_driver','livox_ros_driver_node')
    messages=[('pcl_msgs',src['pcl-msgs']/'msg')]
    for name,msgsrc in messages:
        d=BUILD/'messages'/name;d.mkdir(parents=True,exist_ok=True);shutil.copytree(msgsrc,d/'msg',dirs_exist_ok=True)
        write(d/'package.xml',f'<package><name>{name}</name><version>0.0.1</version><description>Unmodified upstream message definitions</description><maintainer email="local@example.invalid">livo2</maintainer><license>BSD</license><buildtool_depend>catkin</buildtool_depend><build_depend>message_generation</build_depend><build_depend>std_msgs</build_depend><build_depend>sensor_msgs</build_depend><run_depend>message_runtime</run_depend><run_depend>std_msgs</run_depend><run_depend>sensor_msgs</run_depend></package>')
        write(d/'CMakeLists.txt','cmake_minimum_required(VERSION 3.5)\nproject('+name+')\nfind_package(catkin REQUIRED COMPONENTS std_msgs sensor_msgs message_generation)\nadd_message_files(FILES '+' '.join(p.name for p in (d/'msg').glob('*.msg'))+')\ngenerate_messages(DEPENDENCIES std_msgs sensor_msgs)\ncatkin_package(CATKIN_DEPENDS message_runtime std_msgs sensor_msgs)\n')
        build(name,d)
    for name in ['vikit_common','vikit_ros']:build(name,src['vikit']/name,name)
    build('fast-livo2',src['fast-livo2'],'fastlivo_mapping');build('image-decode',ROOT/'runtime/decode')
    from spinnaker import prepare
    spin=prepare('amd64' if native else 'arm64')
    common+=['-DSPINNAKER_ROOT='+str(spin)]
    build('flir-camera',ROOT/'runtime/flir')
    if native:build('camera-calib',src['camera-calib'])


if __name__=='__main__':main(int(sys.argv[1]) if len(sys.argv)>1 else 4,sys.argv[2] if len(sys.argv)>2 else 'board')
