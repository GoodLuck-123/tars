'use strict';
const $=id=>document.getElementById(id), canvas=$('cloud');
let meta=null,current=0,desired=0,playing=false,busy=false,opening=false,generation=0,anchor=0,anchorTime=0,lastImageURL=null,firstFit=true;
const fail=e=>{playing=false;updatePlay();$('error').hidden=false;$('error').textContent=e.message||String(e)};
const format=t=>{const m=Math.floor(t/60),s=t-m*60;return `${String(m).padStart(2,'0')}:${s.toFixed(3).padStart(6,'0')}`};
async function json(url){const r=await fetch(url);const v=await r.json();if(!r.ok)throw Error(v.error||'读取录包失败');return v}
function multiply(a,b){const c=new Float32Array(16);for(let col=0;col<4;col++)for(let row=0;row<4;row++)for(let k=0;k<4;k++)c[col*4+row]+=a[k*4+row]*b[col*4+k];return c}
const norm=a=>{const l=Math.hypot(...a)||1;return a.map(x=>x/l)},cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a.reduce((s,x,i)=>s+x*b[i],0);
class CloudView{
 constructor(){
  this.gl=canvas.getContext('webgl',{antialias:true,preserveDrawingBuffer:true});if(!this.gl)throw Error('浏览器未能开启 WebGL。请开启浏览器硬件加速，或换 Chrome / Firefox 后重试。');
  const gl=this.gl;const shader=(type,code)=>{const s=gl.createShader(type);gl.shaderSource(s,code);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s};
  this.program=gl.createProgram();
  gl.attachShader(this.program,shader(gl.VERTEX_SHADER,`attribute vec4 point;uniform mat4 matrix;uniform float size;uniform float colorMode;uniform vec2 zrange;varying vec3 rgb;void main(){gl_Position=matrix*vec4(point.xyz,1.);gl_PointSize=size;float t=point.w;if(colorMode>.5&&colorMode<1.5)t=clamp((point.z-zrange.x)/max(.01,zrange.y-zrange.x),0.,1.);if(colorMode>1.5)t=clamp(length(point.xyz)/30.,0.,1.);rgb=mix(vec3(.22,.65,.62),vec3(1.,.84,.39),sqrt(clamp(t,0.,1.)));if(point.w<0.)rgb=vec3(.16,.25,.21);if(point.w< -1.5)rgb=vec3(.9,.35,.28);if(point.w< -2.5)rgb=vec3(.44,.76,.36);if(point.w< -3.5)rgb=vec3(.39,.65,.91);}`));
  gl.attachShader(this.program,shader(gl.FRAGMENT_SHADER,`precision mediump float;varying vec3 rgb;uniform float roundPoint;void main(){if(roundPoint>.5&&distance(gl_PointCoord,vec2(.5))>.5)discard;gl_FragColor=vec4(rgb,1.);}`));gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
  this.loc={};for(const name of ['matrix','size','colorMode','zrange','roundPoint'])this.loc[name]=gl.getUniformLocation(this.program,name);this.point=gl.getAttribLocation(this.program,'point');this.buffer=gl.createBuffer();this.grid=gl.createBuffer();
  const grid=[];for(let n=-10;n<=10;n++){grid.push(-10,n,0,-1,10,n,0,-1,n,-10,0,-1,n,10,0,-1)}grid.push(0,0,0,-2,2,0,0,-2,0,0,0,-3,0,2,0,-3,0,0,0,-4,0,0,2,-4);this.gridCount=grid.length/4;gl.bindBuffer(gl.ARRAY_BUFFER,this.grid);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(grid),gl.STATIC_DRAW);
  this.count=0;this.target=[0,0,0];this.distance=12;this.azimuth=-2.3;this.elevation=.55;this.bounds=[[-1,-1,-1],[1,1,1]];
  let drag=null;canvas.addEventListener('contextmenu',e=>e.preventDefault());canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,pan:e.button===2||e.shiftKey};canvas.setPointerCapture(e.pointerId)});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;if(drag.pan){const scale=this.distance*.0015;this.target[0]+=(-Math.sin(this.azimuth)*dx+Math.cos(this.azimuth)*Math.sin(this.elevation)*dy)*scale;this.target[1]+=(Math.cos(this.azimuth)*dx+Math.sin(this.azimuth)*Math.sin(this.elevation)*dy)*scale;this.target[2]-=Math.cos(this.elevation)*dy*scale}else{this.azimuth-=dx*.007;this.elevation=Math.max(-1.5,Math.min(1.5,this.elevation+dy*.007))}this.render()});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>drag=null);
  canvas.addEventListener('wheel',e=>{e.preventDefault();this.distance=Math.max(.2,Math.min(2000,this.distance*Math.exp(e.deltaY*.001)));this.render()},{passive:false});
  canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();fail(Error('3D 渲染上下文已丢失，请刷新页面重新加载。'))});new ResizeObserver(()=>this.render()).observe(canvas);
 }
 fit(){this.target=this.bounds[0].map((x,i)=>(x+this.bounds[1][i])/2);this.distance=Math.max(2,Math.hypot(...this.bounds[0].map((x,i)=>x-this.bounds[1][i]))*1.5);this.azimuth=-2.3;this.elevation=.55;this.render()}
 set(points,info){const gl=this.gl;this.count=points.length/4;this.bounds=info.bounds;gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,points,gl.DYNAMIC_DRAW);if(firstFit){firstFit=false;this.fit()}else this.render()}
 render(){
  const gl=this.gl;if(!gl)return;const rect=canvas.getBoundingClientRect(),ratio=Math.min(window.devicePixelRatio||1,2),width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height}gl.viewport(0,0,width,height);gl.clearColor(.063,.102,.09,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.useProgram(this.program);
  const eye=this.target.map((x,i)=>x+this.distance*[Math.cos(this.elevation)*Math.cos(this.azimuth),Math.cos(this.elevation)*Math.sin(this.azimuth),Math.sin(this.elevation)][i]);const z=norm(eye.map((x,i)=>x-this.target[i])),x=norm(cross([0,0,1],z)),y=cross(z,x);const view=new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]);const f=1/Math.tan(Math.PI/8),near=.02,far=10000;const projection=new Float32Array([f/(width/height),0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);gl.uniformMatrix4fv(this.loc.matrix,false,multiply(projection,view));gl.uniform1f(this.loc.colorMode,Number($('color').value));gl.uniform2f(this.loc.zrange,this.bounds[0][2],this.bounds[1][2]);
  gl.enableVertexAttribArray(this.point);const draw=(buffer,count,kind,round)=>{gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.vertexAttribPointer(this.point,4,gl.FLOAT,false,16,0);gl.uniform1f(this.loc.roundPoint,round);gl.drawArrays(kind,0,count)};
  gl.uniform1f(this.loc.size,1);draw(this.grid,this.gridCount,gl.LINES,0);gl.uniform1f(this.loc.size,Number($('point-size').value)*ratio);if(this.count)draw(this.buffer,this.count,gl.POINTS,1);
 }
}
let cloud;
try{cloud=new CloudView()}catch(e){$('canvas-error').hidden=false;$('canvas-error').textContent=e.message;fail(e)}
function updatePlay(){$('play').textContent=playing?'暂停':'播放';$('play').setAttribute('aria-pressed',String(playing))}
function updateTime(info){$('timeline').value=current;$('time').textContent=`${format(meta.times[current])} / ${format(meta.duration_s)}`;$('frame-info').textContent=`第 ${current+1} / ${meta.frames} 帧 · ${info.valid_points.toLocaleString()} 个非零有限点`;$('point-count').textContent=`显示 ${info.shown_points.toLocaleString()} 点${info.downsample_stride>1?' · 已抽样':''}`;canvas.dataset.frame=String(current);canvas.dataset.points=String(info.shown_points)}
async function loadFrame(index){
 if(!meta||opening)return;desired=Math.max(0,Math.min(meta.frames-1,index));if(busy)return;busy=true;const wanted=desired,version=generation;let imageURL=null;
 try{
  const response=await fetch(`/api/frame?index=${wanted}`);if(!response.ok){const error=await response.json();throw Error(error.error)}const buffer=await response.arrayBuffer(),length=new DataView(buffer).getUint32(0,true),info=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,4,length))),pointOffset=4+length;
  const points=new Float32Array(buffer,pointOffset,info.points_bytes/4);
  if(info.camera.state==='ready'){try{imageURL=URL.createObjectURL(new Blob([new Uint8Array(buffer,pointOffset+info.points_bytes,info.image_bytes)],{type:info.camera.mime}));const image=new Image();image.src=imageURL;await image.decode()}catch(e){if(imageURL)URL.revokeObjectURL(imageURL);imageURL=null;info.camera={state:'error',message:'图像内容损坏或浏览器无法解码，点云继续播放。'}}}
  if(version!==generation){if(imageURL)URL.revokeObjectURL(imageURL);return}
  current=wanted;cloud?.set(points,info);updateTime(info);if(lastImageURL)URL.revokeObjectURL(lastImageURL);lastImageURL=imageURL;$('camera-image').hidden=!imageURL;$('camera-empty').hidden=!!imageURL;
  if(imageURL){$('camera-image').src=imageURL;$('camera-status').textContent=`图像与点云记录时差 ${Math.round(info.camera.delta_s*1000)} ms`}
  else{const gap=info.camera.state==='gap',error=info.camera.state==='error';$('camera-empty').querySelector('h3').textContent=gap?'当前时刻没有匹配图像':error?'当前相机帧无法解码':'本包没有相机数据';$('camera-empty').querySelector('p').textContent=gap?'时间差超过允许范围，未显示旧图像。':error?info.camera.message:'接入后，图像将在同一时间轴播放。';$('camera-status').textContent=gap?`最近图像相差 ${Math.round(Math.abs(info.camera.delta_s)*1000)} ms`:error?'点云播放继续，图像格式需检查':'已预留 raw / 压缩图像接口'}
  $('camera-image').dataset.state=info.camera.state;$('error').hidden=true;
 }catch(e){if(imageURL)URL.revokeObjectURL(imageURL);if(version===generation)fail(e)}finally{busy=false;if(!opening&&(version!==generation||desired!==wanted))loadFrame(desired)}
}
function setPlaying(value){if(!meta||!cloud||opening)return;playing=value;if(playing&&current===meta.frames-1){desired=0;loadFrame(0);anchorTime=0}else anchorTime=meta.times[desired];anchor=performance.now();updatePlay()}
function seek(index){playing=false;updatePlay();loadFrame(index)}
function tick(now){if(meta&&playing){const t=anchorTime+(now-anchor)/1000*Number($('speed').value);let lo=0,hi=meta.frames;while(lo<hi){const mid=(lo+hi)>>1;if(meta.times[mid]<=t)lo=mid+1;else hi=mid}const n=Math.max(0,lo-1);if(n!==desired)loadFrame(n);if(t>=meta.duration_s){playing=false;updatePlay()}}requestAnimationFrame(tick)}
async function openRecording(id){
 playing=false;opening=true;meta=null;updatePlay();generation++;$('recordings').disabled=true;
 for(const id of ['play','previous','next','timeline','speed'])$(id).disabled=true;
 $('source').textContent='正在读取…';
 try{
  const value=await json(id===undefined?'/api/meta':`/api/open?id=${id}`);meta=value;current=desired=0;firstFit=true;
  $('cloud-topic').textContent=meta.lidar_topic;$('camera-topic').textContent=meta.image_topic||'未包含相机流';$('source').textContent=meta.image_frames?'LiDAR + 相机':'仅 LiDAR';$('recordings').title=meta.path;$('timeline').max=meta.frames-1;
  opening=false;for(const id of ['previous','next','timeline','speed'])$(id).disabled=false;$('play').disabled=!cloud;await loadFrame(0);
 }catch(e){$('source').textContent='录包读取失败';throw e}finally{opening=false;$('recordings').disabled=false}
}
$('play').onclick=()=>setPlaying(!playing);$('previous').onclick=()=>seek(current-1);$('next').onclick=()=>seek(current+1);$('timeline').oninput=e=>seek(Number(e.target.value));$('speed').onchange=()=>{anchorTime=meta.times[current];anchor=performance.now()};$('color').onchange=()=>cloud?.render();$('point-size').oninput=()=>cloud?.render();$('reset').onclick=()=>cloud?.fit();$('recordings').onchange=e=>openRecording(e.target.value).catch(fail);
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','BUTTON'].includes(document.activeElement.tagName))return;if(e.code==='Space'){e.preventDefault();setPlaying(!playing)}if(e.code==='ArrowLeft'){e.preventDefault();seek(current-1)}if(e.code==='ArrowRight'){e.preventDefault();seek(current+1)}});
(async()=>{try{const choices=await json('/api/catalog');$('recordings').replaceChildren(...choices.map(c=>{const o=document.createElement('option');o.value=c.id;o.textContent=c.label;return o}));await openRecording();requestAnimationFrame(tick)}catch(e){fail(e)}})();
