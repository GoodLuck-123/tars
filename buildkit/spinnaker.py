"""Pinned, visible proprietary SDK packages, extracted without system installation."""
import json,subprocess,tarfile
from pathlib import Path
from fetch import ROOT,sha
BASE=ROOT/'third_party/spinnaker'
def prepare(arch):
    lock=BASE/'sdk.lock.json';rec=next(x for x in json.loads(lock.read_text())['packages'] if x['architecture']==arch);archive=BASE/rec['file'];dest=BASE/arch
    if not archive.exists():subprocess.run(['curl','-fL','--retry','3','--max-time','180',rec['url'],'-o',archive],check=True)
    if sha(archive)!=rec['sha256']:raise RuntimeError('Spinnaker SDK checksum mismatch: '+str(archive))
    if not (dest/'opt/spinnaker/include/Spinnaker.h').exists():
        dest.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if Path(m.name).is_absolute() or '..' in Path(m.name).parts or m.issym() or m.islnk():raise RuntimeError('Unsafe SDK archive')
            t.extractall(dest/'packages')
        for deb in (dest/'packages').rglob('*.deb'):
            if deb.name.startswith(('libspinnaker_','libspinnaker-dev_','libgentl_','spinnaker_')):subprocess.run(['dpkg-deb','-x',deb,dest],check=True)
    return dest/'opt/spinnaker'
