#include <pcl/point_types.h>
#include <pcl/point_cloud.h>
#include <pcl/filters/voxel_grid.h>
#include <iostream>
int main(){
 for(int n=0;n<30;++n){
  pcl::PointCloud<pcl::PointXYZINormal>::Ptr input(new pcl::PointCloud<pcl::PointXYZINormal>), output(new pcl::PointCloud<pcl::PointXYZINormal>);
  input->resize(2);(*input)[0].x=1000;(*input)[0].y=1000;(*input)[0].z=1000;(*input)[1].x=-1000;(*input)[1].y=-1000;(*input)[1].z=-1000;
  pcl::VoxelGrid<pcl::PointXYZINormal> f;f.setLeafSize(.1,.1,.1);f.setInputCloud(input);f.filter(*output);
 }
 std::cout<<"complete\n";
}
