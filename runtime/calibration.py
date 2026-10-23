"""Run the upstream targetless calibration without GUI or parameter overwrite."""
import json
import math
from pathlib import Path
import time
from monitor import write_json


def run(cfg,job,install,spawn,ros,alive,state,processes):
    ros(['rosparam','set','/use_sim_time','false']);ros(['rosparam','load',job/'calibration.yaml'])
    spawn('calibration',[install/'bin/lidar_camera_calib']);state('calibrating')
    while processes['calibration'].poll() is None:
        alive();state('calibrating');time.sleep(.5)
    if processes['calibration'].returncode:raise RuntimeError('Calibration executable failed')
    p=job/'extrinsic.txt'
    rows=[list(map(float,line.split(','))) for line in p.read_text().splitlines()]
    if len(rows)!=4 or any(len(r)!=4 or not all(math.isfinite(x) for x in r) for r in rows):raise RuntimeError('Invalid calibration matrix')
    if any(abs(rows[3][i]-[0,0,0,1][i])>1e-6 for i in range(4)):raise RuntimeError('Invalid homogeneous transform')
    for i in range(3):
        for j in range(3):
            if abs(sum(rows[k][i]*rows[k][j] for k in range(3))-(i==j))>.01:raise RuntimeError('Non-orthogonal calibration rotation')
    determinant=sum(rows[0][j]*(rows[1][(j+1)%3]*rows[2][(j+2)%3]-rows[1][(j+2)%3]*rows[2][(j+1)%3]) for j in range(3))
    if abs(determinant-1)>.01:raise RuntimeError('Invalid calibration rotation determinant')
    if not (job/'extrinsic.txt.optimized.png').is_file():raise RuntimeError('Missing calibration projection image')
    write_json(job/'calibration-report.json',dict(method='livox_camera_calib',transform='T_camera_lidar',matrix=rows,candidate_only=True,accuracy_verified=False))
