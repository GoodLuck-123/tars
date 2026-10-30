#include <ros/ros.h>
#include <sensor_msgs/CompressedImage.h>
#include <cv_bridge/cv_bridge.h>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/imgproc.hpp>
#include <algorithm>
int main(int argc, char** argv) {
  ros::init(argc, argv, "livo_image_decode");
  ros::NodeHandle nh;
  cv::setNumThreads(nh.param("/runtime/opencv_threads", 1));
  ROS_INFO("OpenCV threads: %d", cv::getNumThreads());
  std::vector<double> k, d;
  if (!nh.getParam("/decoder/K", k) || !nh.getParam("/decoder/D", d) || k.size()!=9) { ROS_FATAL("Missing decoder calibration"); return 2; }
  int width=nh.param("/decoder/width",1280), height=nh.param("/decoder/height",1024);
  int ow=nh.param("/decoder/output_width",width), oh=nh.param("/decoder/output_height",height);
  const std::string output_encoding=nh.param<std::string>("/decoder/output_encoding", "bgr8");
  if (output_encoding!="bgr8" && output_encoding!="mono8") { ROS_FATAL("Unsupported decoder output encoding: %s", output_encoding.c_str()); return 2; }
  if (std::min({width,height,ow,oh})<1) { ROS_FATAL("Invalid decoder dimensions"); return 2; }
  cv::Mat map1, map2;
  auto pub = nh.advertise<sensor_msgs::Image>("image", 20);
  auto publish = [&](const cv::Mat& image, const std_msgs::Header& header) {
    if (image.empty()) { ROS_ERROR("Image decode returned an empty frame"); return; }
    if (map1.empty()) {
      cv::Mat K = cv::Mat(k).reshape(1,3);
      cv::Mat inputK=K.clone(), outputK=K.clone();
      inputK.row(0)*=double(image.cols)/width; inputK.row(1)*=double(image.rows)/height;
      outputK.row(0)*=double(ow)/width; outputK.row(1)*=double(oh)/height;
      cv::initUndistortRectifyMap(inputK, cv::Mat(d), cv::Mat(), outputK, cv::Size(ow,oh), CV_16SC2, map1, map2);
      ROS_INFO("Decoded %dx%d, rectified output %dx%d; full distortion model applied", image.cols, image.rows,ow,oh);
    }
    cv::Mat rectified; cv::remap(image, rectified, map1, map2, cv::INTER_LINEAR);
    if (output_encoding=="mono8" && rectified.channels()==3) cv::cvtColor(rectified, rectified, cv::COLOR_BGR2GRAY);
    pub.publish(cv_bridge::CvImage(header, output_encoding, rectified).toImageMsg());
  };
  ros::Subscriber sub;
  if (nh.param<std::string>("/decoder/input_transport", "compressed")=="raw") {
    sub=nh.subscribe<sensor_msgs::Image>("raw",20,[&](const sensor_msgs::ImageConstPtr& msg) {
      try {
        if (int(msg->width)!=width || int(msg->height)!=height) { ROS_ERROR("Raw image dimensions do not match camera configuration"); return; }
        publish(cv_bridge::toCvShare(msg,"bgr8")->image,msg->header);
      } catch (const cv_bridge::Exception& e) { ROS_ERROR("Image conversion failed: %s",e.what()); }
        catch (const cv::Exception& e) { ROS_ERROR("Image rectify failed: %s",e.what()); }
    });
  } else {
    sub=nh.subscribe<sensor_msgs::CompressedImage>("compressed",20,[&](const sensor_msgs::CompressedImageConstPtr& msg) {
      try {
        int mode=output_encoding=="mono8" ? cv::IMREAD_GRAYSCALE : ((ow*2==width && oh*2==height) ? cv::IMREAD_REDUCED_COLOR_2 : cv::IMREAD_COLOR);
        publish(cv::imdecode(msg->data,mode),msg->header);
      } catch (const cv::Exception& e) { ROS_ERROR("Image decode failed: %s",e.what()); }
    });
  }
  ros::spin();
}
