"""Capture workflow plugged into the common detached task supervisor."""
import json
from pathlib import Path
import time


def run(cfg,job,install,spawn,ros,alive,state,processes,master):
    topics=cfg['capture_topics'];cap=cfg['capture'];(job/'data').mkdir(exist_ok=True)
    ros(['rosparam','set','/use_sim_time','true' if cfg['mode']=='capture_replay' else 'false'])
    spawn('probe',['python3',install/'runtime/capture_probe.py','observe',job])
    args=['rosbag','record','--buffsize',str(cap['buffer_mib']),'--split','--size',str(cap['split_mib']),'-O',job/'data/sensors',*[v['topic'] for v in topics.values()],*cap['extra_topics']]
    spawn('record',args)
    if cfg['mode']=='capture_replay':
        args=['rosbag','play','--clock','--quiet','--delay','2','--rate','1','--start',str(cfg['start_s']),'--duration',str(cap['replay_duration_s']),cfg['bag']]
        spawn('play',args)
    else:
        ros(['rosparam','load',job/'livox.yaml']);spawn('livox',[install/'bin/livox_ros_driver_node','/livox/lidar:='+cfg['topics']['lidar'],'/livox/imu:='+cfg['topics']['imu']])
        if 'image' in topics:spawn('camera',cap['camera_command'])
    state('waiting for sensor data');deadline=time.monotonic()+cap['startup_timeout_s'];ready=False;started=None
    while True:
        alive()
        path=job/'capture-state.json';snapshot=json.loads(path.read_text()) if path.exists() else {};streams=snapshot.get('streams',{})
        for k,s in streams.items():
            if s['type']!=topics[k]['type']:raise RuntimeError('Wrong sensor type: '+k)
            if s['stamp_regressions']:raise RuntimeError('Sensor timestamp moved backwards: '+k)
            if ready and time.monotonic()-s['last_arrival']>cap['stall_timeout_s']:raise RuntimeError('Sensor stream stopped: '+k)
        if not ready and all(streams.get(k,{}).get('messages',0)>1 for k in topics):ready=True;started=time.monotonic()
        state('recording' if ready else 'waiting for sensor data',capture=snapshot)
        if not ready and time.monotonic()>deadline:raise RuntimeError('Sensor startup timeout; missing: '+', '.join(k for k in topics if k not in streams))
        if cfg['mode']=='capture_replay' and processes['play'].poll() is not None:
            if processes['play'].returncode:raise RuntimeError('Replay source failed')
            if not ready:raise RuntimeError('Replay ended without all sensor streams')
            time.sleep(1);break
        if ready and cfg['mode']!='capture_replay' and cap['duration_s'] is not None and time.monotonic()-started>=cap['duration_s']:break
        time.sleep(.5)
