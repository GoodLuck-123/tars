"""Verify and install an extracted bundle into an independent user prefix."""
import hashlib,json,os,platform,shutil,subprocess,sys
from pathlib import Path


def verify(root):
    root=Path(root).resolve();manifest=json.loads((root/'files.json').read_text())
    for name,expected in manifest.items():
        p=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not p.resolve().is_relative_to(root):raise ValueError('Invalid manifest path')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise RuntimeError('Checksum mismatch: '+name)
    return hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]


def install(source,prefix):
    if platform.machine()!='aarch64':raise RuntimeError('This package requires Linux aarch64')
    source=Path(source).resolve();prefix=Path(prefix).expanduser().resolve()
    if prefix in [Path('/'),Path.home()]:raise ValueError('Select a dedicated install prefix')
    ident=verify(source);dest=prefix/'releases'/ident;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        temp=prefix/'releases'/('.install-'+ident)
        if temp.exists():shutil.rmtree(temp)
        shutil.copytree(source,temp);verify(temp)
        subprocess.run(['bash','-ec','source "$1/env.sh"; python3 -c "import rospy, rosbag, yaml"; rosversion -d','verify',str(temp)],check=True)
        temp.rename(dest)
    else:verify(dest)
    current=prefix/'current'
    if current.exists() and not current.is_symlink():raise RuntimeError('Refusing to replace a real current directory')
    link=prefix/'current.new';link.unlink(missing_ok=True);link.symlink_to('releases/'+ident);os.replace(link,current)
    print(str(dest));return dest


if __name__=='__main__':install(sys.argv[1],sys.argv[2])
