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
  if (std::min({width,height,ow,oh})<1) { ROS_FATAL("Invalid decoder dimensions"); return 2; }
  cv::Mat map1, map2;
  auto pub = nh.advertise<sensor_msgs::Image>("image", 20);
  auto sub = nh.subscribe<sensor_msgs::CompressedImage>("compressed", 20,
    [&](const sensor_msgs::CompressedImageConstPtr& msg) {
      try {
        int mode=(ow*2==width && oh*2==height) ? cv::IMREAD_REDUCED_COLOR_2 : cv::IMREAD_COLOR;
        auto image = cv::imdecode(msg->data, mode);
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
        pub.publish(cv_bridge::CvImage(msg->header, "bgr8", rectified).toImageMsg());
      } catch (const cv::Exception& e) { ROS_ERROR("Image decode failed: %s", e.what()); }
    });
  ros::spin();
}
