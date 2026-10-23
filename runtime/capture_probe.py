"""Observe ROS wire headers without decoding or modifying sensor payloads."""
import json
from pathlib import Path
import struct
import sys
import threading
import time
import rospy
from monitor import write_json


def observe(job):
    cfg=json.loads((job/'config.json').read_text());streams={};lock=threading.Lock()
    rospy.init_node('capture_probe',disable_signals=False)
    def callback(msg,label):
        now=time.monotonic();typ=msg._connection_header.get('type');buf=msg._buff
        with lock:
            s=streams.setdefault(label,dict(messages=0,bytes=0,first_arrival=now,stamp_regressions=0,sequence_gaps=0))
            seq,sec,nsec=struct.unpack_from('<III',buf);stamp=sec+nsec/1e9
            if s['messages'] and stamp<s['last_stamp']:s['stamp_regressions']+=1
            if s['messages'] and seq>s['last_sequence']+1:s['sequence_gaps']+=seq-s['last_sequence']-1
            s.update(messages=s['messages']+1,bytes=s['bytes']+len(buf),first_stamp=s.get('first_stamp',stamp),last_stamp=stamp,last_arrival=now,last_sequence=seq,type=typ)
    subscribers=[rospy.Subscriber(v['topic'],rospy.AnyMsg,lambda m,k=k:callback(m,k),queue_size=1000,buff_size=16*1024*1024,tcp_nodelay=True) for k,v in cfg['capture_topics'].items()]
    while not rospy.is_shutdown():
        with lock:
            snapshot={k:{**s,'hz':(s['messages']-1)/max(.001,s['last_arrival']-s['first_arrival'])} for k,s in streams.items()}
        write_json(job/'capture-state.json',dict(heartbeat=time.time(),streams=snapshot));time.sleep(.5)


def verify(job):
    import rosbag
    cfg=json.loads((job/'config.json').read_text());bags=sorted((job/'data').glob('*.bag'));counts={};types={}
    if not bags or list((job/'data').glob('*.active')):raise RuntimeError('Missing or unfinished bag')
    for p in bags:
        with rosbag.Bag(str(p)) as bag:
            for topic,info in bag.get_type_and_topic_info().topics.items():
                counts[topic]=counts.get(topic,0)+info.message_count;types[topic]=info.msg_type
    for spec in cfg['capture_topics'].values():
        if counts.get(spec['topic'],0)<1 or types.get(spec['topic'])!=spec['type']:raise RuntimeError('Missing/wrong recorded stream: '+spec['topic'])
    report=dict(mode=cfg['mode'],hardware_verified=cfg['mode']=='capture',calibration_verified=cfg.get('calibration_verified',False),bags=[dict(file=p.name,bytes=p.stat().st_size) for p in bags],counts=counts,types=types)
    write_json(job/'capture-report.json',report)


if __name__=='__main__':
    action,job=sys.argv[1],Path(sys.argv[2])
    (observe if action=='observe' else verify)(job)
