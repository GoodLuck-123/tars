#include <ros/ros.h>
#include <sensor_msgs/CompressedImage.h>
#include <cv_bridge/cv_bridge.h>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/calib3d.hpp>
#include <opencv2/imgproc.hpp>
int main(int argc, char** argv) {
  ros::init(argc, argv, "livo_image_decode");
  ros::NodeHandle nh;
  std::vector<double> k, d;
  if (!nh.getParam("/decoder/K", k) || !nh.getParam("/decoder/D", d) || k.size()!=9) { ROS_FATAL("Missing decoder calibration"); return 2; }
  cv::Mat map1, map2;
  auto pub = nh.advertise<sensor_msgs::Image>("image", 20);
  auto sub = nh.subscribe<sensor_msgs::CompressedImage>("compressed", 20,
    [&](const sensor_msgs::CompressedImageConstPtr& msg) {
      try {
        auto image = cv::imdecode(msg->data, cv::IMREAD_COLOR);
        if (image.empty()) { ROS_ERROR("Image decode returned an empty frame"); return; }
        if (map1.empty()) {
          cv::Mat K = cv::Mat(k).reshape(1,3);
          cv::initUndistortRectifyMap(K, cv::Mat(d), cv::Mat(), K, image.size(), CV_16SC2, map1, map2);
          ROS_INFO("Decoded image size %dx%d; full distortion model applied", image.cols, image.rows);
        }
        cv::Mat rectified; cv::remap(image, rectified, map1, map2, cv::INTER_LINEAR);
        pub.publish(cv_bridge::CvImage(msg->header, "bgr8", rectified).toImageMsg());
      } catch (const cv::Exception& e) { ROS_ERROR("Image decode failed: %s", e.what()); }
    });
  ros::spin();
}
