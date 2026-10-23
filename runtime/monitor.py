"""Deterministic incremental log monitor. Raw logs remain unchanged."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
LEVEL = re.compile(r'\b(FATAL|ERROR|WARN(?:ING)?)\b', re.I)
PROGRESS = re.compile(r'LIVO_PROGRESS stamp=([\d.]+) mode=(\d+) lidar_queue=(\d+) image_queue=(\d+)')


def write_json(path, value):
    path=Path(path);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


class Monitor:
    def __init__(self, root, repeat_s=10):
        self.root=Path(root);self.dest=self.root/'monitor';self.dest.mkdir(exist_ok=True)
        self.repeat_s=repeat_s;self.cursors={};self.recent={};self.changed=False
        path=self.dest/'state.json'
        self.state=json.loads(path.read_text()) if path.exists() else dict(schema=1,rules_version=1,lines=0,warnings=0,errors=0,visual_updates=0,processed=0,alerts={})
        self.state['count_basis']='log records, exact cross-source line copies removed; differently formatted mirrors may repeat'
        self.seq=self.state.get('event_seq',0)
        cp=self.dest/'cursors.json'
        if cp.exists():self.cursors=json.loads(cp.read_text())
        self.events=(self.dest/'events.jsonl').open('a',buffering=1)

    def emit(self, level, message, source, offset, **extra):
        self.seq+=1
        self.events.write(json.dumps(dict(id=self.seq,time=time.time(),level=level,message=message,source=source,offset=offset,**extra),ensure_ascii=False)+'\n')
        self.changed=True

    def line(self, text, source, offset, now=None):
        now=time.monotonic() if now is None else now
        text=ANSI.sub('',text).strip();self.state['lines']+=1
        # Sources may contain copies of the same ROS message. Count canonical events once.
        message=re.sub(r'^(?:\[[^\]]*\]\s*)+', '',text)
        digest=hashlib.sha256(text.encode()).hexdigest()
        previous=self.recent.get(digest)
        if previous and previous[0]!=source and now-previous[1]<2:
            self.state['cross_source_copies']=self.state.get('cross_source_copies',0)+1;return
        self.recent[digest]=(source,now)
        if len(self.recent)>10000:self.recent={k:v for k,v in self.recent.items() if now-v[1]<2}
        m=PROGRESS.search(text)
        if m:
            stamp=float(m[1])
            self.state.update(processed_stamp=stamp,mode=int(m[2]),lidar_queue=int(m[3]),image_queue=int(m[4]),progress_wall=time.time())
            self.state['processed']+=1;self.changed=True
        if 'LIVO_VISUAL_UPDATE' in text:self.state['visual_updates']+=1;self.changed=True
        level=LEVEL.search(text)
        if not level and any(x in text for x in ['Traceback (most recent call last)', 'Segmentation fault','terminate called','No space left on device','process has died']):
            severity='ERROR'
        elif level:severity='WARN' if level[1].upper().startswith('WARN') else level[1].upper()
        else:return
        self.state['warnings' if severity=='WARN' else 'errors']+=1
        # Normalize changing numbers only for aggregation; retain representative source text.
        signature=severity+':'+re.sub(r'[-+]?\d+(?:\.\d+)?','<n>',message)
        key=hashlib.sha256(signature.encode()).hexdigest()[:16]
        alert=self.state['alerts'].setdefault(key,dict(level=severity,message=text,count=0,first_time=time.time(),last_notice=0))
        alert.update(count=alert['count']+1,last_time=time.time(),source=source,offset=offset)
        if alert['count']==1 or time.time()-alert['last_notice']>=self.repeat_s:
            self.emit(severity,text,source,offset,count=alert['count'],signature=key);alert['last_notice']=time.time()
        self.changed=True

    def poll(self):
        for base in [self.root/'logs',self.root/'algorithm/Log']:
            if not base.exists():continue
            for path in sorted(base.rglob('*')):
                if not path.is_file() or path.suffix not in {'.log','.txt','.jsonl'}:continue
                key=str(path.relative_to(self.root));stat=path.stat();cursor=self.cursors.get(key,dict(inode=stat.st_ino,offset=0))
                if cursor['inode']!=stat.st_ino or stat.st_size<cursor['offset']:cursor=dict(inode=stat.st_ino,offset=0)
                with path.open('rb') as f:
                    f.seek(cursor['offset'])
                    while True:
                        offset=f.tell();raw=f.readline()
                        if not raw or not raw.endswith(b'\n'):break
                        self.line(raw.decode(errors='replace'),key,offset);cursor['offset']=f.tell()
                self.cursors[key]=cursor
        self.state.update(event_seq=self.seq,heartbeat=time.time(),sources=len(self.cursors),status='running')
        write_json(self.dest/'state.json',self.state);write_json(self.dest/'cursors.json',self.cursors)


def main(root,repeat_s=10):
    mon=Monitor(root,repeat_s)
    while not (Path(root)/'stop-monitor').exists():mon.poll();time.sleep(.5)
    mon.poll();mon.state['status']='complete';write_json(mon.dest/'state.json',mon.state)


if __name__=='__main__':main(sys.argv[1],float(sys.argv[2]) if len(sys.argv)>2 else 10)
