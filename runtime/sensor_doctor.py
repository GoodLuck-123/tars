"""Read-only target-side environment, network, and live capture checks."""
import fcntl
import ipaddress
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time


def check(root,livox,prefix='',host_epoch=None):
    root=Path(root);prefix=Path(prefix) if prefix else root/'current';problems=[]
    report=dict(architecture=platform.machine(),workspace=str(root),release=prefix.resolve().name,problems=problems)
    report['host_offset_estimate_s']=round(time.time()-float(host_epoch),2) if host_epoch else None
    if host_epoch and abs(report['host_offset_estimate_s'])>5:problems.append('目标端时钟与主机相差超过 5 秒；默认下一次空闲启动会校时，请勿在录制中调整')
    report['free_gib']=round(shutil.disk_usage(root if root.exists() else root.parent).free/2**30,2)
    driver=prefix/'bin/livox_ros_driver_node'
    report['driver_installed']=driver.is_file()
    report['video_nodes']={p.parent.name:p.read_text().strip() for p in Path('/sys/class/video4linux').glob('video*/name')}
    if not driver.is_file():problems.append('缺少 Livox 驱动，请先 ./livo deploy')
    else:
        try:
            sys.path.insert(0,str(prefix/'runtime'))
            from install import verify
            if (prefix/'files.json').exists():
                ident=verify(prefix)
                if ident!=prefix.resolve().name:raise RuntimeError('运行包目录与清单不匹配')
                report['manifest_verified']=True
            else:
                report['native_build']=json.loads((prefix/'build.json').read_text())
            cmd=['bash','-ec','source "$1/env.sh"; python3 -c "import rospy, rosbag, yaml"; ldd "$1/bin/livox_ros_driver_node"','doctor',str(prefix)]
            result=subprocess.run(cmd,text=True,capture_output=True,timeout=30)
            report['runtime_libraries']=result.stdout.strip()
            if result.returncode or 'not found' in result.stdout:raise RuntimeError((result.stderr or result.stdout).strip())
            report['environment_ready']=True
        except Exception as e:problems.append('私有运行环境检查失败：'+str(e))
    ip=livox.get('ip');network=dict(lidar_ip=ip);report['network']=network
    if ip:
        ip=str(ipaddress.IPv4Address(ip))
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                sock.connect((ip,55000));source=sock.getsockname()[0]
            network['source_ip']=source
            addresses=json.loads(subprocess.check_output(['ip','-j','addr'],text=True))
            for item in addresses:
                for addr in item.get('addr_info',[]):
                    if addr.get('local')==source:network.update(interface=item['ifname'],source_cidr=source+'/'+str(addr['prefixlen']),link_up='UP' in item.get('flags',[]))
            if not network.get('link_up'):problems.append('到 AVIA 的网卡未 UP')
            if shutil.which('ping'):
                ping=subprocess.run(['ping','-c','1','-W','1',ip],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
                network['ping_reachable']=ping.returncode==0
        except Exception as e:problems.append('AVIA 网络路径检查失败：'+str(e))
    else:problems.append('请在 project.yaml 的 capture.livox.ip 设置 AVIA IP')
    busy=False
    if (root/'run.lock').exists():
        with (root/'run.lock').open('r') as guard:
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(guard,fcntl.LOCK_UN)
            except BlockingIOError:busy=True
    report['task_active']=busy
    live=False
    if busy:
        try:
            job=Path(json.loads((root/'latest.json').read_text())['job']);state=json.loads((job/'state.json').read_text());cfg=json.loads((job/'config.json').read_text())
            task=dict(run_id=job.name,phase=state['phase'],mode=cfg.get('mode'),heartbeat_age_s=round(time.time()-state['heartbeat'],2));report['task']=task
            if task['heartbeat_age_s']>10:problems.append('任务心跳过期，请检查板端 supervisor 日志')
            if cfg.get('mode') in {'capture','capture_lidar'}:
                live=True
                if cfg['capture']['livox'].get('ip')!=ip:problems.append('当前任务 AVIA IP 与 project.yaml 不一致；先 stop 再启动')
                snapshot=json.loads((job/'capture-state.json').read_text()) if (job/'capture-state.json').exists() else {}
                task['streams']={k:dict(messages=v['messages'],hz=round(v['hz'],2),age_s=round(time.monotonic()-v['last_arrival'],2),type=v['type'],stamp_regressions=v['stamp_regressions']) for k,v in snapshot.get('streams',{}).items()}
                required=cfg['capture_topics'];task['receiving']=state['phase']=='recording' and all(k in task['streams'] and task['streams'][k]['messages']>1 and task['streams'][k]['age_s']<cfg['capture']['stall_timeout_s'] and task['streams'][k]['type']==required[k]['type'] and not task['streams'][k]['stamp_regressions'] for k in required)
                if (job/'device.json').exists():network['device']=json.loads((job/'device.json').read_text())
                network['verification']='live_messages' if task['receiving'] else 'task_not_receiving'
                if not task['receiving']:problems.append('当前采集任务尚未就绪或传感器断流')
        except Exception as e:problems.append('读取当前任务失败：'+str(e))
    if ip and not live and report.get('environment_ready'):
        try:
            from livox_discovery import discover
            device=discover(ip,timeout_s=4)
            if livox.get('broadcast_codes') and device['broadcast_code'] not in livox['broadcast_codes']:raise RuntimeError('IP 对应广播码与配置不一致')
            network.update(device=device,verification='discovery_only')
        except Exception as e:problems.append('AVIA 发现失败：'+str(e))
    report['ok']=not problems
    return report


if __name__=='__main__':print(json.dumps(check(sys.argv[1],json.loads(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else '',sys.argv[4] if len(sys.argv)>4 else None),ensure_ascii=False))
