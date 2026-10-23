"""Build only the ARM64 components used by dataset LIVO."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'.build';SDK=ROOT/'.cache/sysroot';TOOL=ROOT/'.cache/toolchain';STAGE=BUILD/'stage';SRC=BUILD/'src'


def run(cmd):
    print('+',' '.join(map(str,cmd)),flush=True);subprocess.run(list(map(str,cmd)),check=True)


def main(jobs=4):
    from fetch import packages,sources,relocate
    for name,d in [('toolchain',TOOL),('ros1-sdk',SDK),('pcl',SDK)]:packages(name,d)
    sources();relocate(SDK)
    SRC.mkdir(parents=True,exist_ok=True);STAGE.mkdir(exist_ok=True)
    for name in ['fast-livo2','sophus','vikit','perception-pcl','pcl-msgs','livox-driver']:
        if not (SRC/name).exists():shutil.copytree(ROOT/'.cache/sources'/name,SRC/name)
    # Patch only build portability and output destination. Algorithms remain upstream.
    for p in [SRC/'fast-livo2/CMakeLists.txt',SRC/'vikit/vikit_common/CMakeLists.txt',SRC/'vikit/vikit_ros/CMakeLists.txt']:
        s=p.read_text().replace('-mcpu=native','-march=armv8-a').replace('-march=native','-march=armv8-a').replace('-mtune=native','')
        if 'fast-livo2' in str(p):
            s=s.replace('  eigen_conversions\n','').replace('set(Sophus_LIBRARIES libSophus.so)','').replace('  pcl_ros\n','  livox_ros_driver\n').replace('find_package(PCL REQUIRED)','find_package(PCL REQUIRED COMPONENTS common io filters kdtree search octree sample_consensus)')
            s=s.replace('  include\n','  include\n  '+str(SRC/'perception-pcl/pcl_conversions/include')+'\n')
        p.write_text(s)
    p=SRC/'sophus/sophus/so2.cpp';s=p.read_text().replace('unit_complex_.real() = 1.;','unit_complex_.real(1.);').replace('unit_complex_.imag() = 0.;','unit_complex_.imag(0.);');p.write_text(s)
    p=SRC/'fast-livo2/include/common_lib.h'
    text=(ROOT/'.cache/sources/fast-livo2/include/common_lib.h').read_text()
    text=text.replace('using namespace std;', '#include <cstdlib>\n#undef ROOT_DIR\n#define ROOT_DIR (std::getenv("LIVO_OUTPUT_DIR") ? std::getenv("LIVO_OUTPUT_DIR") : "./")\n\nusing namespace std;')
    p.write_text(text)
    p=SRC/'fast-livo2/src/LIVMapper.cpp';text=(ROOT/'.cache/sources/fast-livo2/src/LIVMapper.cpp').read_text()
    text=text.replace('    stateEstimationAndMapping();','    stateEstimationAndMapping();\n    std::cout << std::fixed << std::setprecision(6) << "LIVO_PROGRESS stamp=" << LidarMeasures.last_lio_update_time << " mode=" << int(LidarMeasures.lio_vio_flg) << " lidar_queue=" << lid_raw_data_buffer.size() << " image_queue=" << img_buffer.size() << std::endl;')
    text=text.replace('  if (imu_prop_enable) \n  {','  std::cout << "LIVO_VISUAL_UPDATE" << std::endl;\n  if (imu_prop_enable) \n  {',1)
    text=text.replace('&& pcd_save_interval < 0)', ')')
    p.write_text(text)
    # Record exact adapted source for review and provenance.
    changes={}
    for name in ['fast-livo2','vikit','sophus']:
        pristine=ROOT/'.cache/sources'/name
        for p in (SRC/name).rglob('*'):
            q=pristine/p.relative_to(SRC/name)
            if p.is_file() and q.is_file() and p.read_bytes()!=q.read_bytes():
                import difflib
                changes[str(p.relative_to(SRC))]=''.join(difflib.unified_diff(q.read_text().splitlines(True),p.read_text().splitlines(True),fromfile=str(q.relative_to(ROOT)),tofile=str(p.relative_to(ROOT))))
    (BUILD/'patches.json').write_text(json.dumps(changes,indent=2)+'\n')
    hostlibs=ROOT/'.cache/host-libs';hostlibs.mkdir(exist_ok=True)
    for pattern in ['libisl.so.22','libgmp.so.10','libmpc.so.3','libmpfr.so.6','libbfd*-arm64.so*','libopcodes*-arm64.so*','libctf*-arm64.so*']:
        for p in (TOOL/'usr/lib/x86_64-linux-gnu').glob(pattern):
            d=hostlibs/p.name
            if not d.exists():d.symlink_to(p)
    os.environ.update(LIVO_SYSROOT=str(SDK),LIVO_TOOLCHAIN=str(TOOL),LIVO_STAGE=str(STAGE),LD_LIBRARY_PATH=str(hostlibs),PYTHONPATH=str(SDK/'opt/ros/noetic/lib/python3/dist-packages')+':'+str(SDK/'usr/lib/python3/dist-packages'),PKG_CONFIG_SYSROOT_DIR=str(SDK),PKG_CONFIG_LIBDIR=str(SDK/'usr/lib/aarch64-linux-gnu/pkgconfig'))
    os.environ.pop('CONDA_PREFIX',None)
    common=['-DCMAKE_TOOLCHAIN_FILE='+str(ROOT/'buildkit/aarch64.cmake'),'-DCMAKE_BUILD_TYPE=Release','-DCATKIN_ENABLE_TESTING=OFF','-DPYTHON_EXECUTABLE=/usr/bin/python3','-DCATKIN_DEVEL_PREFIX='+str(STAGE),'-DCMAKE_INSTALL_PREFIX='+str(STAGE),'-DCMAKE_PREFIX_PATH='+str(STAGE)+';'+str(SDK/'opt/ros/noetic')+';'+str(SDK/'usr'),'-DEigen3_DIR='+str(SDK/'usr/share/eigen3/cmake'),'-DCMAKE_EXPORT_COMPILE_COMMANDS=ON','-DPKG_CONFIG_EXECUTABLE=/usr/bin/pkg-config','-DSophus_DIR='+str(BUILD/'sophus')]
    def build(name,source,extra=(),install=False):
        path=BUILD/name
        run(['cmake','-S',source,'-B',path,*common,*extra]);run(['cmake','--build',path,'-j',str(jobs)])
        if install:run(['cmake','--install',path])
    build('sophus',SRC/'sophus',['-DBUILD_TESTS=OFF'],True)
    for name,msgsrc in [('livox_ros_driver',SRC/'livox-driver/livox_ros_driver/msg'),('pcl_msgs',SRC/'pcl-msgs/msg')]:
        d=SRC/(name+'-messages');d.mkdir(exist_ok=True);shutil.copytree(msgsrc,d/'msg',dirs_exist_ok=True)
        (d/'package.xml').write_text(f'<package><name>{name}</name><version>0.0.1</version><description>Upstream message definitions</description><maintainer email="local@example.invalid">livo2</maintainer><license>BSD</license><buildtool_depend>catkin</buildtool_depend><build_depend>message_generation</build_depend><build_depend>std_msgs</build_depend><build_depend>sensor_msgs</build_depend><run_depend>message_runtime</run_depend><run_depend>std_msgs</run_depend><run_depend>sensor_msgs</run_depend></package>')
        (d/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.5)\nproject('+name+')\nfind_package(catkin REQUIRED COMPONENTS std_msgs sensor_msgs message_generation)\nadd_message_files(FILES '+' '.join(p.name for p in (d/'msg').glob('*.msg'))+')\ngenerate_messages(DEPENDENCIES std_msgs sensor_msgs)\ncatkin_package(CATKIN_DEPENDS message_runtime std_msgs sensor_msgs)\n')
        build(name,d)
    for name in ['vikit_common','vikit_ros']:build(name,SRC/'vikit'/name)
    build('fast-livo2',SRC/'fast-livo2')
    build('image-decode',ROOT/'runtime/decode')


if __name__=='__main__':main(int(sys.argv[1]) if len(sys.argv)>1 else 4)
