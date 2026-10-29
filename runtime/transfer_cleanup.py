"""Remove only manifest-listed bags after the host has verified the full transfer."""
import hashlib
import json
from pathlib import Path
import sys
import time


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def cleanup(workspace, job, manifest_sha):
    workspace, job = Path(workspace).resolve(), Path(job)
    if job.is_symlink() or job.resolve().parent != workspace / 'runs':
        raise ValueError('Refusing cleanup outside workspace/runs')
    job = job.resolve()
    if json.loads((job / 'state.json').read_text())['phase'] != 'finished':
        raise RuntimeError('Refusing cleanup of an unfinished task')
    manifest_path = job / 'files.json'
    if sha(manifest_path) != manifest_sha:
        raise RuntimeError('Remote manifest changed; bags retained')
    candidates = []
    for name, expected in json.loads(manifest_path.read_text()).items():
        relative = Path(name)
        if not relative.parts or relative.parts[0] != 'data' or not name.endswith(('.bag', '.bag.active')):
            continue
        path = job / relative
        if '..' in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(job / 'data') or (job / 'data').is_symlink():
            raise ValueError('Unsafe bag path: ' + name)
        if path.exists():
            if sha(path) != expected:
                raise RuntimeError('Remote bag changed; bags retained: ' + name)
            candidates.append((path, path.stat().st_size))
    # Check every candidate before deleting any; retries also accept already removed bags.
    for path, _ in candidates:
        path.unlink()
    receipt = dict(deleted_bags=len(candidates), freed_bytes=sum(size for _, size in candidates), verified_manifest_sha256=manifest_sha, cleaned_at=time.time())
    previous = job / 'bag-cleanup.json'
    if candidates or not previous.exists():
        previous.write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    print(json.dumps(cleanup(*sys.argv[1:])))
