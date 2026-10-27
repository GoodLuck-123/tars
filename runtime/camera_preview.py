"""Loopback MJPEG view: subscribe to a run, or own a camera without recording."""
import atexit,fcntl,json,os,signal,socket,subprocess,threading,time,webbrowser,sys,xmlrpc.client
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import cv2,numpy as np,rospy
from sensor_msgs.msg import Image
job=Path(sys.argv[1]);cfg=json.loads((job/'config.json').read_text());condition=threading.Condition();frame=None;sequence=0;stopped=threading.Event()
gamma=cfg.get('preview_gamma',1.0);lut=np.rint((np.arange(256)/255.)**(1/gamma)*255).astype(np.uint8)
children=[];logs=[]
owner_file=None
def cleanup():
    for p in reversed(children):
        if p.poll() is None:
            os.killpg(p.pid,signal.SIGINT)
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    for f in logs:f.close()
    if owner_file:owner_file.unlink(missing_ok=True)
atexit.register(cleanup)
if '--standalone' in sys.argv:
    guard=(Path(cfg['workspace'])/'run.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    owner_file=Path(cfg['workspace'])/'preview.json';owner_file.write_text(json.dumps(dict(pid=os.getpid())))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    os.environ.update(ROS_MASTER_URI=f'http://127.0.0.1:{port}',ROS_IP='127.0.0.1',ROS_HOME=str(job/'ros-home'),ROS_LOG_DIR=str(job/'ros-log'))
    def launch(name,args):
        log=(job/(name+'.log')).open('w');logs.append(log);p=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True);children.append(p);return p
    launch('master',['roscore','-p',str(port)]);master=xmlrpc.client.ServerProxy(os.environ['ROS_MASTER_URI']);deadline=time.monotonic()+15
    while True:
        try:
            if master.getPid('/camera-preview')[0]==1:break
        except OSError:pass
        if time.monotonic()>deadline:raise RuntimeError('Preview ROS master startup timeout')
        time.sleep(.1)
    subprocess.run(['rosparam','load',str(job/'camera-driver.yaml'),'/livo_camera'],check=True)
    launch('camera',[cfg['install']+'/bin/livo_flir_camera','image_raw:='+cfg['capture_topics']['image']['topic']])
def receive(msg):
    global frame,sequence
    enc=msg.encoding.lower();channels=3 if enc in {'rgb8','bgr8'} else 1
    arr=np.ndarray((msg.height,msg.width,channels),dtype=np.uint8,buffer=msg.data,strides=(msg.step,channels,1))
    if enc.startswith('bayer_'):color=cv2.cvtColor(arr[:,:,0],getattr(cv2,'COLOR_Bayer'+enc[6:-1].upper()+'2BGR'))
    elif enc=='rgb8':color=cv2.cvtColor(arr,cv2.COLOR_RGB2BGR)
    elif enc=='bgr8':color=arr
    elif enc=='mono8':color=arr[:,:,0]
    else:rospy.logerr_throttle(2,'Preview unsupported encoding: '+enc);return
    if msg.width>1200:color=cv2.resize(color,(1200,round(msg.height*1200/msg.width)),interpolation=cv2.INTER_AREA)
    if enc.startswith('bayer_') and gamma!=1:color=cv2.LUT(color,lut)
    ok,data=cv2.imencode('.jpg',color,[cv2.IMWRITE_JPEG_QUALITY,85])
    if ok:
        with condition:frame=data.tobytes();sequence+=1;condition.notify_all()
rospy.init_node('livo_camera_preview',disable_signals=True)
sub=rospy.Subscriber(cfg['capture_topics']['image']['topic'],Image,receive,queue_size=1,buff_size=32*1024*1024)
if '--standalone' in sys.argv:
    with condition:condition.wait_for(lambda:frame is not None,timeout=15)
    if frame is None:raise RuntimeError('相机预览未收到图像，请检查 '+str(job/'camera.log'))
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path not in {'/','/stream'}:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame');self.send_header('Cache-Control','no-store');self.end_headers();seen=-1
        try:
            while not stopped.is_set():
                with condition:
                    condition.wait_for(lambda:sequence!=seen or stopped.is_set(),timeout=2)
                    if frame is None or sequence==seen:continue
                    image=frame;seen=sequence
                self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(image)).encode()+b'\r\n\r\n'+image+b'\r\n');self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);url='http://127.0.0.1:'+str(server.server_port)
cache=Path(sys.argv[2]);cache.write_text(json.dumps(dict(url=url,job=str(job),pid=os.getpid()))+'\n');print('相机实时预览：'+url+'；'+('仅看画面，不录包；' if '--standalone' in sys.argv else '')+'Ctrl-C 关闭预览。',flush=True)
if '--standalone' in sys.argv:
    def watch():
        while not stopped.wait(1):
            if any(p.poll() is not None for p in children):
                print('ERROR 相机预览进程已退出，请检查 '+str(job/'camera.log'),flush=True);server.shutdown();break
    threading.Thread(target=watch,daemon=True).start()
if '--no-open' not in sys.argv:webbrowser.open(url)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:stopped.set();server.server_close();rospy.signal_shutdown('Preview closed')
