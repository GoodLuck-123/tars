"""Download verified archives and extract a private cross SDK; never installs packages."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()


def obtain(item, seeds=()):
    dest = CACHE / 'archives' / item['file']
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if sha(dest) != item['sha256']: raise RuntimeError(f'Checksum mismatch: {dest}')
        return dest
    tmp = dest.with_suffix(dest.suffix + '.part')
    for seed in seeds:
        p = Path(seed) / item['file']
        if p.is_file() and sha(p) == item['sha256']:
            shutil.copy2(p, tmp)
            break
    else:
        subprocess.run(['curl', '-fsSL', '--retry', '3', '--retry-all-errors', '--connect-timeout', '15', '--max-time', '180', item['url'], '-o', str(tmp)], check=True)
    if sha(tmp) != item['sha256']: raise RuntimeError(f'Checksum mismatch: {tmp}')
    tmp.replace(dest)
    return dest


def relocate(root):
    for p in root.rglob('*'):
        if p.is_symlink() and os.readlink(p).startswith('/'):
            target = os.readlink(p);p.unlink();p.symlink_to(os.path.relpath(root / target.lstrip('/'), p.parent))
    pattern = re.compile(r'(?<![}\w])(/usr/(?:lib/aarch64-linux-gnu|include))(?=[/";\s])')
    for p in list((root / 'opt/ros/noetic/share').rglob('*.cmake')) + list((root / 'usr/lib').rglob('*.cmake')):
        text = p.read_text();updated = pattern.sub(lambda m: '${CMAKE_SYSROOT}' + m[1], text)
        if text != updated: p.write_text(updated)


def packages(name, destination, seeds=()):
    lock = ROOT / 'buildkit/locks' / (name + '.json')
    marker = destination / ('.livo-' + name)
    if marker.exists() and marker.read_text().strip() == sha(lock): return
    items = json.loads(lock.read_text())['packages']
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(lambda x: obtain(x, seeds), items))
    destination.mkdir(parents=True, exist_ok=True)
    for p in files: subprocess.run(['dpkg-deb', '-x', str(p), str(destination)], check=True)
    if name != 'toolchain': relocate(destination)
    marker.write_text(sha(lock) + '\n')
    print(f'{name}: {len(items)} verified packages', flush=True)


def sources(seeds=()):
    for item in json.loads((ROOT / 'dependencies.lock').read_text())['sources']:
        dest = CACHE / 'sources' / item['name']
        if (dest / '.livo-source').exists():
            if (dest / '.livo-source').read_text().strip() != item['sha256']: raise RuntimeError(f'Source lock changed: {dest}')
            continue
        archive = obtain(item, seeds)
        dest.mkdir(parents=True, exist_ok=True)
        # Archives are checksum verified; reject escaping names/links before tar extraction.
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if Path(m.name).is_absolute() or '..' in Path(m.name).parts or m.isdev(): raise ValueError(m.name)
                if m.issym() or m.islnk():
                    if Path(m.linkname).is_absolute() or '..' in Path(m.linkname).parts: raise ValueError(m.linkname)
        subprocess.run(['tar', '-xf', str(archive), '--strip-components=1', '-C', str(dest)], check=True)
        (dest / '.livo-source').write_text(item['sha256'] + '\n')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('--seed', action='append', default=[]);p.add_argument('--packages-only', action='store_true')
    a = p.parse_args()
    for name, target in [('toolchain', 'toolchain'), ('ros1-sdk', 'sysroot'), ('pcl', 'sysroot')]: packages(name, CACHE / target, a.seed)
    if not a.packages_only: sources(a.seed)
