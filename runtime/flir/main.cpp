// Small ROS1 transport adapter using the vendor Spinnaker acquisition API.
#include <Spinnaker.h>
#include <SpinGenApi/SpinnakerGenApi.h>
#include <ros/ros.h>
#include <sensor_msgs/Image.h>
#include <sensor_msgs/CameraInfo.h>
#include <std_msgs/String.h>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <map>
using namespace Spinnaker;
using namespace Spinnaker::GenApi;
static void enumeration(INodeMap& m,const char* key,const std::string& value,bool required=true){
 CEnumerationPtr n=m.GetNode(key);CEnumEntryPtr e=IsAvailable(n)?n->GetEntryByName(value.c_str()):nullptr;
 if(!IsWritable(n)||!IsReadable(e)){if(required)throw std::runtime_error(std::string("Unsupported setting ")+key+"="+value);return;}
 n->SetIntValue(e->GetValue());
}
static void number(INodeMap& m,const char* key,double value){
 CFloatPtr n=m.GetNode(key);if(!IsWritable(n)||value<n->GetMin()||value>n->GetMax())throw std::runtime_error(std::string("Unsupported setting ")+key);n->SetValue(value);
}
static std::string text(INodeMap& m,const char* key){CStringPtr n=m.GetNode(key);return IsReadable(n)?std::string(n->GetValue()):"";}
int main(int argc,char** argv){
 SystemPtr system;CameraList cameras;CameraPtr camera;bool initialized=false,acquiring=false;int result=0;
 try{
  system=System::GetInstance();cameras=system->GetCameras();
  if(argc>1&&std::string(argv[1])=="--probe"){
   std::cout<<"{\"cameras\":[";for(unsigned i=0;i<cameras.GetSize();i++){auto c=cameras.GetByIndex(i);auto& m=c->GetTLDeviceNodeMap();if(i)std::cout<<",";std::cout<<"{\"serial\":"<<std::quoted(text(m,"DeviceSerialNumber"))<<",\"model\":"<<std::quoted(text(m,"DeviceModelName"))<<"}";}std::cout<<"]}"<<std::endl;
  }else{
   ros::init(argc,argv,"livo_camera");ros::NodeHandle nh,p("~");std::string serial,format,frame_id,exposure_auto,gain_auto,white_balance;
   double fps,exposure,gain,auto_exposure_max,auto_gain_max,white_red,white_blue;bool gamma_enabled;int width,height,offset_x,offset_y;
   p.param<std::string>("serial",serial,"");p.param<std::string>("pixel_format",format,"BayerRG8");p.param<std::string>("frame_id",frame_id,"camera_optical_frame");
   p.param<std::string>("exposure_auto",exposure_auto,"Continuous");p.param<std::string>("gain_auto",gain_auto,"Continuous");p.param<std::string>("white_balance_auto",white_balance,"Continuous");
   p.param("fps",fps,10.0);p.param("exposure_us",exposure,10000.0);p.param("gain_db",gain,0.0);p.param("width",width,0);p.param("height",height,0);p.param("offset_x",offset_x,0);p.param("offset_y",offset_y,0);
   p.param("auto_exposure_max_us",auto_exposure_max,30000.0);p.param("auto_gain_max_db",auto_gain_max,18.0);p.param("gamma_enabled",gamma_enabled,false);
   p.param("white_balance_red",white_red,1.0);p.param("white_balance_blue",white_blue,1.0);
   if(cameras.GetSize()==0)throw std::runtime_error("No camera; check USB3 connection and device permissions");
   if(serial.empty()&&cameras.GetSize()!=1)throw std::runtime_error("Multiple cameras; set capture.camera.serial in project.yaml");
   camera=serial.empty()?cameras.GetByIndex(0):cameras.GetBySerial(serial);if(!camera||!camera->IsValid())throw std::runtime_error("Configured camera serial not found");
   camera->Init();initialized=true;auto& m=camera->GetNodeMap();serial=text(m,"DeviceSerialNumber");
   enumeration(m,"TriggerMode","Off");enumeration(m,"AcquisitionMode","Continuous");enumeration(m,"PixelFormat",format);
   auto integer=[&](const char* key,int value){CIntegerPtr n=m.GetNode(key);if(!IsWritable(n)||value<n->GetMin()||value>n->GetMax()||(value-n->GetMin())%n->GetInc())throw std::runtime_error(std::string("Unsupported setting ")+key);n->SetValue(value);};
   integer("OffsetX",0);integer("OffsetY",0);CIntegerPtr w=m.GetNode("Width"),h=m.GetNode("Height");integer("Width",width?width:w->GetMax());integer("Height",height?height:h->GetMax());integer("OffsetX",offset_x);integer("OffsetY",offset_y);
   CBooleanPtr enable=m.GetNode("AcquisitionFrameRateEnable");if(!IsWritable(enable))throw std::runtime_error("Frame rate control unavailable");enable->SetValue(true);number(m,"AcquisitionFrameRate",fps);
   enumeration(m,"ExposureAuto",exposure_auto);if(exposure_auto=="Off")number(m,"ExposureTime",exposure);enumeration(m,"GainAuto",gain_auto);if(gain_auto=="Off")number(m,"Gain",gain);enumeration(m,"BalanceWhiteAuto",white_balance,false);
   if(exposure_auto!="Off")number(m,"AutoExposureExposureTimeUpperLimit",auto_exposure_max);
   if(gain_auto!="Off")number(m,"AutoExposureGainUpperLimit",auto_gain_max);
   CBooleanPtr gamma=m.GetNode("GammaEnable");if(IsWritable(gamma))gamma->SetValue(gamma_enabled);else if(gamma_enabled)throw std::runtime_error("GammaEnable unavailable");
   if(white_balance=="Off"){enumeration(m,"BalanceRatioSelector","Red");number(m,"BalanceRatio",white_red);enumeration(m,"BalanceRatioSelector","Blue");number(m,"BalanceRatio",white_blue);}
   // Fail early rather than silently lowering the requested frame rate for a long exposure.
   if(exposure_auto=="Off"&&exposure>1e6/fps)throw std::runtime_error("exposure_us exceeds frame period; shorten exposure or lower fps in project.yaml");
   CFloatPtr actual_fps=m.GetNode("AcquisitionResultingFrameRate");if(IsReadable(actual_fps)&&actual_fps->GetValue()<fps*.98)throw std::runtime_error("Camera cannot sustain requested fps with these settings; check exposure, resolution and USB bandwidth");
   bool chunks=false;CBooleanPtr chunk_mode=m.GetNode("ChunkModeActive");if(IsWritable(chunk_mode)){
    chunk_mode->SetValue(true);for(const char* key:{"Timestamp","FrameID","ExposureTime","Gain"}){CEnumerationPtr selector=m.GetNode("ChunkSelector");CEnumEntryPtr e=IsAvailable(selector)?selector->GetEntryByName(key):nullptr;if(IsWritable(selector)&&IsReadable(e)){selector->SetIntValue(e->GetValue());CBooleanPtr enabled=m.GetNode("ChunkEnable");if(IsWritable(enabled))enabled->SetValue(true);}}chunks=true;
   }
   auto& stream=camera->GetTLStreamNodeMap();enumeration(stream,"StreamBufferHandlingMode","OldestFirst");
   std::map<std::string,std::string> encodings={{"BayerRG8","bayer_rggb8"},{"BayerBG8","bayer_bggr8"},{"BayerGR8","bayer_grbg8"},{"BayerGB8","bayer_gbrg8"},{"Mono8","mono8"},{"RGB8","rgb8"},{"BGR8","bgr8"}};
   if(!encodings.count(format))throw std::runtime_error("Unsupported pixel format for ROS image");
   auto images=nh.advertise<sensor_msgs::Image>("image_raw",2);auto metadata=nh.advertise<std_msgs::String>("metadata",10);auto info=nh.advertise<sensor_msgs::CameraInfo>("camera_info",2);
   camera->BeginAcquisition();acquiring=true;uint32_t seq=0;uint64_t previous=0,missing=0,incomplete=0;bool have_previous=false;
   ROS_INFO_STREAM("FLIR acquisition ready: serial="<<serial<<" format="<<format<<" fps="<<fps<<" timestamp=host_receive; calibration unverified");
   while(ros::ok()){
    auto image=camera->GetNextImage(2000);const auto received=ros::Time::now();
    if(image->IsIncomplete()){incomplete++;ROS_WARN_STREAM_THROTTLE(2,"Incomplete camera frame, status="<<image->GetImageStatus()<<" total="<<incomplete);image->Release();continue;}
    const auto fid=image->GetFrameID(),device_time=image->GetTimeStamp();if(have_previous&&fid>previous+1){missing+=fid-previous-1;ROS_WARN_STREAM_THROTTLE(2,"Camera frame ID gap; cumulative="<<missing);}previous=fid;have_previous=true;
    sensor_msgs::Image out;out.header.seq=seq++;out.header.stamp=received;out.header.frame_id=frame_id;out.width=image->GetWidth();out.height=image->GetHeight();out.encoding=encodings.at(format);out.is_bigendian=0;out.step=image->GetStride();const auto size=static_cast<size_t>(out.step)*out.height;
    if(image->GetBufferSize()<size)throw std::runtime_error("Camera image buffer shorter than stride*height");out.data.resize(size);std::memcpy(out.data.data(),image->GetData(),size);
    double exposure_now=0,gain_now=0;bool exact=false;
    try{if(chunks){auto data=image->GetChunkData();exposure_now=data.GetExposureTime();gain_now=data.GetGain();exact=true;}}catch(const Spinnaker::Exception&){}
    if(!exact){CFloatPtr e=m.GetNode("ExposureTime"),g=m.GetNode("Gain");if(IsReadable(e))exposure_now=e->GetValue();if(IsReadable(g))gain_now=g->GetValue();}
    std::ostringstream json;json<<std::setprecision(17)<<"{\"seq\":"<<out.header.seq<<",\"receive_stamp_ns\":"<<received.toNSec()<<",\"device_timestamp_ns\":"<<device_time<<",\"device_frame_id\":"<<fid<<",\"exposure_us\":"<<exposure_now<<",\"gain_db\":"<<gain_now<<",\"exposure_source\":"<<std::quoted(exact?"chunk":"node_read")<<",\"frame_id_gaps\":"<<missing<<",\"incomplete_frames\":"<<incomplete<<",\"serial\":"<<std::quoted(serial)<<"}";
    std_msgs::String meta;meta.data=json.str();sensor_msgs::CameraInfo ci;ci.header=out.header;ci.width=out.width;ci.height=out.height;ci.distortion_model="plumb_bob"; // K remains zero: uncalibrated, never reuse dataset intrinsics.
    image->Release();images.publish(out);metadata.publish(meta);info.publish(ci);ros::spinOnce();
   }
  }
 }catch(const std::exception& e){std::cerr<<"ERROR FLIR: "<<e.what()<<std::endl;result=1;}
 try{if(acquiring)camera->EndAcquisition();if(initialized)camera->DeInit();}catch(const std::exception& e){std::cerr<<"ERROR FLIR shutdown: "<<e.what()<<std::endl;result=1;}
 camera=nullptr;cameras.Clear();if(system)system->ReleaseInstance();return result;
}
