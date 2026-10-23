"""Assemble a relocatable ROS1/FAST-LIVO2 runtime without system glibc."""
import hashlib,json,os,re,shutil,subprocess,tarfile
from pathlib import Path
from fetch import sha
ROOT=Path(__file__).resolve().parents[1];SDK=ROOT/'.cache/sysroot';BUILD=ROOT/'.build';OUT=ROOT/'dist/payload'
SYSTEM={'ld-linux-aarch64.so.1','libc.so.6','libm.so.6','libpthread.so.0','libdl.so.2','librt.so.1','libresolv.so.2','libutil.so.1','libnss_files.so.2','libnss_dns.so.2','libanl.so.1'}


def input_fingerprint():
    paths=[ROOT/'dependencies.lock']
    for directory in ['buildkit','runtime','patches']:
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    # Include editable third-party sources, not just their Git commit IDs.
    if (ROOT/'dependencies.lock').exists():
        for rec in json.loads((ROOT/'dependencies.lock').read_text())['sources']:
            if rec.get('reference_only'):continue
            paths.extend(p for p in (ROOT/rec['directory']).rglob('*') if p.is_file() and '.git' not in p.parts and (p.suffix in {'.cpp','.h','.hpp','.cmake'} or p.name in {'CMakeLists.txt','package.xml'}))
    files={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(paths))}
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def copytree(source,dest):
    for p in source.rglob('*'):
        rel=p.relative_to(source)
        if not p.is_file() or any(x in {'include','cmake','pkgconfig','__pycache__','test','tests','doc','docs','vtk','vtkmodules','PyQt5','mpi4py'} for x in rel.parts) or p.suffix in {'.a','.pyc'}:continue
        d=dest/rel;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,d)
        if p.open('rb').read(23).startswith(b'#!/usr/bin/python3\n'):d.write_bytes(d.read_bytes().replace(b'#!/usr/bin/python3\n',b'#!/usr/bin/env python3\n',1))


def needed(p):
    return re.findall(r'Shared library: \[(.*?)\]',subprocess.check_output(['readelf','-d',str(p)],text=True))


def main():
    if OUT.exists():shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    copytree(SDK/'opt/ros/noetic',OUT/'ros')
    copytree(SDK/'usr/lib/python3/dist-packages',OUT/'python-extra')
    copytree(SDK/'usr/lib/python3.8',OUT/'python/lib/python3.8')
    (OUT/'python/bin').mkdir(parents=True);shutil.copy2(SDK/'usr/bin/python3.8',OUT/'python/bin/python3')
    d=OUT/'ros/bin/rosversion';shutil.copy2(SDK/'usr/bin/rosversion',d);d.write_bytes(d.read_bytes().replace(b'#!/usr/bin/python3\n',b'#!/usr/bin/env python3\n',1))
    (OUT/'install.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\nroot=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)\nexec /usr/bin/python3 "$root/runtime/install.py" "$root" "${1:-$HOME/livo2-workspace}"\n')
    (OUT/'install.sh').chmod(0o755)
    (OUT/'bin').mkdir();shutil.copy2(BUILD/'stage/lib/fast_livo/fastlivo_mapping',OUT/'bin/fastlivo_mapping');shutil.copy2(BUILD/'image-decode/livo_image_decode',OUT/'bin/livo_image_decode')
    shutil.copy2(BUILD/'stage/lib/livox_ros_driver/livox_ros_driver_node',OUT/'bin/livox_ros_driver_node')
    for name in ['livox_ros_driver','pcl_msgs']:
        copytree(BUILD/'stage/lib/python3/dist-packages'/name,OUT/'ros/lib/python3/dist-packages'/name)
    copytree(ROOT/'runtime',OUT/'runtime')
    (OUT/'env.sh').write_text('''#!/usr/bin/env bash
export LIVO_INSTALL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
unset PYTHONPATH PYTHONHOME ROS_PACKAGE_PATH AMENT_PREFIX_PATH CMAKE_PREFIX_PATH ROS_HOSTNAME
export PYTHONNOUSERSITE=1 PYTHONHOME="$LIVO_INSTALL_ROOT/python"
export PYTHONPATH="$LIVO_INSTALL_ROOT/ros/lib/python3/dist-packages:$LIVO_INSTALL_ROOT/python-extra"
export PATH="$LIVO_INSTALL_ROOT/bin:$LIVO_INSTALL_ROOT/python/bin:$LIVO_INSTALL_ROOT/ros/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$LIVO_INSTALL_ROOT/lib:$LIVO_INSTALL_ROOT/ros/lib"
export CMAKE_PREFIX_PATH="$LIVO_INSTALL_ROOT/ros"
export ROS_PACKAGE_PATH="$LIVO_INSTALL_ROOT/ros/share" ROS_ROOT="$LIVO_INSTALL_ROOT/ros/share/ros" ROS_ETC_DIR="$LIVO_INSTALL_ROOT/ros/etc/ros"
export ROS_DISTRO=noetic ROS_VERSION=1 ROS_PYTHON_VERSION=3 ROS_IP=127.0.0.1
''')
    directories=[BUILD/'stage/lib',BUILD/'sophus',SDK/'opt/ros/noetic/lib',SDK/'usr/lib/aarch64-linux-gnu',SDK/'lib/aarch64-linux-gnu',SDK/'usr/lib/aarch64-linux-gnu/blas',SDK/'usr/lib/aarch64-linux-gnu/lapack',SDK/'usr/lib']
    pending=[]
    for p in OUT.rglob('*'):
        if p.is_file() and p.open('rb').read(4)==b'\x7fELF':pending+=needed(p)
    lib=OUT/'lib';lib.mkdir();copied=set()
    while pending:
        name=pending.pop()
        if name in SYSTEM or name in copied:continue
        p=next((d/name for d in directories if (d/name).is_file()),None)
        if p is None:raise RuntimeError('Missing runtime library: '+name)
        if subprocess.check_output(['readelf','-h',str(p)],text=True).find('AArch64')<0:raise RuntimeError('Wrong architecture: '+str(p))
        shutil.copy2(p,lib/name);copied.add(name);pending+=needed(p)
    for p in OUT.rglob('*'):
        if p.is_file() and p.open('rb').read(4)==b'\x7fELF':subprocess.run([str(ROOT/'.cache/toolchain/usr/bin/aarch64-linux-gnu-strip'),'--strip-unneeded',str(p)],check=True,env={**os.environ,'LD_LIBRARY_PATH':str(ROOT/'.cache/host-libs')})
    provenance={'input_fingerprint':input_fingerprint(),'platform_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'sources':json.loads((ROOT/'dependencies.lock').read_text()),'package_locks':{p.name:sha(p) for p in (ROOT/'buildkit/locks').glob('*.json')},'patches':json.loads((BUILD/'patches.json').read_text()),'system_libs':sorted(SYSTEM),'runtime_libraries':sorted(copied)}
    (OUT/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    for rec in json.loads((ROOT/'dependencies.lock').read_text())['sources']:
        for p in (ROOT/rec['directory']).glob('*'):
            if p.is_file() and p.name.upper().startswith(('LICENSE','COPYING')):
                d=OUT/'licenses'/rec['name']/p.name;d.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,d)
    copytree(ROOT/'buildkit/locks',OUT/'rebuild-locks')
    manifest={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    (OUT/'files.json').write_text(json.dumps(manifest,indent=2)+'\n')
    ident=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16];archive=ROOT/'dist'/f'livo2-{ident}-arm64.tar.gz'
    with tarfile.open(archive,'w:gz') as t:t.add(OUT,arcname='install')
    release=dict(input_fingerprint=input_fingerprint(),id=ident,path=str(archive),sha256=sha(archive),files=len(manifest),bytes=archive.stat().st_size)
    (ROOT/'dist/release.json').write_text(json.dumps(release,indent=2)+'\n');print(json.dumps(release))


if __name__=='__main__':main()
