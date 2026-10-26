"""Offline ROS1 bag playback. No ROS master, sensor connection, or external assets."""
from bisect import bisect_left
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
from pathlib import Path
import struct
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser
import numpy as np
from PIL import Image
from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_types_from_msg, get_typestore

ASSETS=Path(__file__).parent
LIDAR_TYPES={'livox_ros_driver/msg/CustomMsg','sensor_msgs/msg/PointCloud2'}
IMAGE_TYPES={'sensor_msgs/msg/Image','sensor_msgs/msg/CompressedImage'}


def bag_files(path):
    path=Path(path).expanduser().resolve()
    if path.is_file() and path.suffix=='.bag':return [path]
    if path.is_dir():
        files=list(path.glob('*.bag')) or list((path/'data').glob('*.bag'))
        if files:return sorted(files)
    raise ValueError('未找到完整 bag：'+str(path))


class Recording:
    def __init__(self,path,settings,topics):
        self.path=Path(path).resolve();self.readers=[];self.frames=[];self.images=[];self.settings=settings;self.store=get_typestore(Stores.ROS1_NOETIC)
        try:
            for file in bag_files(path):
                r=Reader(file);r.open();self.readers.append(r)
            connections=[c for r in self.readers for c in r.connections]
            lidars=[c for c in connections if c.msgtype in LIDAR_TYPES]
            if not lidars:raise ValueError('包中没有 Livox CustomMsg 或 PointCloud2 点云')
            lidar=next((c for c in lidars if c.topic==topics['lidar']),lidars[0])
            cameras=[c for c in connections if c.msgtype in IMAGE_TYPES]
            camera=next((c for key in ['compressed_image','raw_image','image'] for c in cameras if c.topic==topics.get(key)),cameras[0] if cameras else None)
            for r in self.readers:
                for c in r.connections:
                    dest=self.frames if c.topic==lidar.topic and c.msgtype==lidar.msgtype else self.images if camera and c.topic==camera.topic and c.msgtype==camera.msgtype else None
                    if dest is not None:
                        self.store.register(get_types_from_msg(c.msgdef.data,c.msgtype))
                        dest.extend((entry.time,r,c) for entry in r.indexes[c.id])
            self.frames.sort(key=lambda x:x[0]);self.images.sort(key=lambda x:x[0])
            if not self.frames:raise ValueError('包中没有 Livox CustomMsg 或 PointCloud2 点云')
            self.start=self.frames[0][0];self.times=[x[0] for x in self.frames];self.image_times=[x[0] for x in self.images]
        except Exception:self.close();raise

    def close(self):
        for r in self.readers:r.close()

    def metadata(self):
        return dict(name=self.path.name,path=str(self.path),frames=len(self.frames),duration_s=(self.times[-1]-self.start)/1e9,times=[(t-self.start)/1e9 for t in self.times],lidar_topic=self.frames[0][2].topic,image_topic=self.images[0][2].topic if self.images else None,image_frames=len(self.images),time_basis='bag_record_time',point_limit=self.settings['max_points'])

    @staticmethod
    def raw(entry):
        stamp,reader,connection=entry
        return next(reader.messages(connections=[connection],start=stamp,stop=stamp+1))[2]

    def point_data(self,entry):
        raw=self.raw(entry)
        if entry[2].msgtype=='livox_ros_driver/msg/CustomMsg':
            frame_len=struct.unpack_from('<I',raw,12)[0];at=16+frame_len
            declared=struct.unpack_from('<I',raw,at+8)[0];count=struct.unpack_from('<I',raw,at+16)[0]
            if count!=declared or len(raw)!=at+20+count*19:raise ValueError('CustomMsg 点数或长度损坏')
            dtype=np.dtype([('offset','<u4'),('x','<f4'),('y','<f4'),('z','<f4'),('reflectivity','u1'),('tag','u1'),('line','u1')])
            cloud=np.frombuffer(raw,dtype=dtype,count=count,offset=at+20)
            points=np.column_stack([cloud['x'],cloud['y'],cloud['z'],cloud['reflectivity']/255])
        else:
            msg=self.store.deserialize_ros1(raw,entry[2].msgtype);names={f.name:f for f in msg.fields};endian='>' if msg.is_bigendian else '<'
            codes={1:'i1',2:'u1',3:'i2',4:'u2',5:'i4',6:'u4',7:'f4',8:'f8'}
            fields=['x','y','z'];color='intensity' if 'intensity' in names else 'reflectivity' if 'reflectivity' in names else None
            if not all(f in names for f in fields):raise ValueError('PointCloud2 缺少 xyz 字段')
            if color:fields.append(color)
            dtype=np.dtype(dict(names=fields,formats=[endian+codes[names[f].datatype] for f in fields],offsets=[names[f].offset for f in fields],itemsize=msg.point_step))
            cloud=np.ndarray((msg.height,msg.width),dtype=dtype,buffer=msg.data,strides=(msg.row_step,msg.point_step)).reshape(-1)
            points=np.column_stack([cloud[f] for f in ['x','y','z']]+[np.clip(cloud[color]/255,0,1) if color else np.full(cloud.size,.7)])
        total=len(points);valid=np.isfinite(points).all(axis=1)&(np.linalg.norm(points[:,:3],axis=1)>.05)
        points=points[valid];valid_count=len(points);stride=max(1,math.ceil(valid_count/self.settings['max_points']));points=points[::stride].astype('<f4')
        bounds=np.percentile(points[:,:3],[2,98],axis=0).tolist() if len(points) else [[-1,-1,-1],[1,1,1]]
        return points.tobytes(),dict(total_points=total,valid_points=valid_count,shown_points=len(points),bounds=bounds,downsample_stride=stride)

    def image_data(self,entry):
        msg=self.store.deserialize_ros1(self.raw(entry),entry[2].msgtype)
        if entry[2].msgtype=='sensor_msgs/msg/CompressedImage':
            data=bytes(msg.data)
            if data.startswith(b'\xff\xd8'):return data,'image/jpeg'
            if data.startswith(b'\x89PNG'):return data,'image/png'
            raise ValueError('暂不支持该压缩图像格式：'+msg.format)
        enc=msg.encoding.lower();channels={'rgb8':3,'bgr8':3,'rgba8':4,'bgra8':4,'mono8':1,'8uc1':1,'mono16':1,'16uc1':1}
        if enc not in channels:raise ValueError('暂不支持相机编码：'+msg.encoding)
        n=channels[enc];depth=2 if enc in {'mono16','16uc1'} else 1
        arr=np.ndarray((msg.height,msg.width,n),dtype=('>' if msg.is_bigendian else '<')+'u'+str(depth),buffer=msg.data,strides=(msg.step,n*depth,depth))
        if depth==2:arr=(arr>>8).astype(np.uint8)
        if enc.startswith('bgr'):arr=arr[:,:,[2,1,0,3] if n==4 else [2,1,0]]
        image=Image.fromarray(arr[:,:,0] if n==1 else arr)
        out=BytesIO();image.save(out,format='PNG');return out.getvalue(),'image/png'

    @lru_cache(maxsize=12)
    def frame(self,index):
        entry=self.frames[index];points,stats=self.point_data(entry);image=b'';camera=dict(state='absent')
        if self.images:
            at=bisect_left(self.image_times,entry[0]);candidates=[n for n in [at-1,at] if 0<=n<len(self.images)];n=min(candidates,key=lambda n:abs(self.image_times[n]-entry[0]));delta=(self.image_times[n]-entry[0])/1e9
            camera=dict(state='gap',delta_s=delta)
            if abs(delta)<=self.settings['image_tolerance_s']:
                try:image,mime=self.image_data(self.images[n]);camera.update(state='ready',mime=mime)
                except Exception as e:camera.update(state='error',message=str(e))
        meta=dict(index=index,time_s=(entry[0]-self.start)/1e9,points_bytes=len(points),image_bytes=len(image),camera=camera,**stats)
        header=json.dumps(meta,ensure_ascii=False).encode();header+=b' '*((-len(header))%4)
        return struct.pack('<I',len(header))+header+points+image


def catalog(c,selected):
    entries=[Path(selected).resolve()]
    base=Path(c['results_dir'])
    entries.extend(sorted((p.parent for p in base.glob('*/data/*.bag')),key=lambda p:p.parent.stat().st_mtime,reverse=True))
    if Path(c['dataset']['path']).is_file():entries.append(Path(c['dataset']['path']))
    return list(dict.fromkeys(entries))


def serve(c,path=None,open_browser=True):
    settings=c.get('viewer',{});settings={'max_points':100000,'image_tolerance_s':.15,**settings}
    if type(settings['max_points']) is not int or settings['max_points']<1 or not math.isfinite(settings['image_tolerance_s']) or settings['image_tolerance_s']<0:raise ValueError('viewer 参数必须为有效正数')
    if path is None:
        path=settings.get('bag')
        if not path:
            recent=sorted(Path(c['results_dir']).glob('*/data/*.bag'),key=lambda p:p.parent.parent.stat().st_mtime,reverse=True)
            path=recent[0].parent if recent else c['dataset']['path']
    path=Path(c['_config_dir'])/Path(path).expanduser();paths=catalog(c,path);lock=threading.Lock();recording=Recording(path,settings,c['topics'])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,body,mime='application/json',status=200):
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            nonlocal recording
            url=urlparse(self.path);query=parse_qs(url.query)
            try:
                with lock:
                    if url.path=='/api/meta':body=json.dumps(recording.metadata(),ensure_ascii=False).encode();mime='application/json'
                    elif url.path=='/api/catalog':body=json.dumps([dict(id=i,label=p.parent.name+' / '+p.name,path=str(p)) for i,p in enumerate(paths)],ensure_ascii=False).encode();mime='application/json'
                    elif url.path=='/api/open':
                        ident=int(query['id'][0])
                        if not 0<=ident<len(paths):raise ValueError('无效的录制编号')
                        replacement=Recording(paths[ident],settings,c['topics']);old=recording;recording=replacement;old.frame.cache_clear();old.close();body=json.dumps(recording.metadata(),ensure_ascii=False).encode();mime='application/json'
                    elif url.path=='/api/frame':
                        index=int(query.get('index',['0'])[0])
                        if not 0<=index<len(recording.frames):raise ValueError('帧编号越界')
                        body=recording.frame(index);mime='application/octet-stream'
                    elif url.path in {'/','/index.html','/app.js','/style.css'}:
                        name='index.html' if url.path=='/' else url.path[1:];body=(ASSETS/name).read_bytes();mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.split('.')[-1]]
                    else:self.reply(b'Not found','text/plain',404);return
                self.reply(body,mime)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as e:
                try:self.reply(json.dumps(dict(error=str(e)),ensure_ascii=False).encode(),status=400)
                except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);url=f'http://127.0.0.1:{server.server_port}'
    cache=Path(__file__).resolve().parents[1]/'.cache';cache.mkdir(exist_ok=True);(cache/'viewer.json').write_text(json.dumps(dict(url=url,bag=str(path)))+'\n')
    print('录包播放器：'+url+'\n'+str(path)+'\n仅本机访问；Ctrl-C 关闭播放器，不影响采集任务。',flush=True)
    if open_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();recording.frame.cache_clear();recording.close()
