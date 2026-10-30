"""Rebase Livox device-time headers to the host epoch for live LIVO only.

The raw topics remain untouched and are recorded for later calibration. This
node preserves relative sensor timing while putting LiDAR/IMU on the same
epoch as the host-timestamped camera stream.
"""
import copy
import sys
import threading
import rospy
from sensor_msgs.msg import Imu
from livox_ros_driver.msg import CustomMsg


class Bridge:
    def __init__(self, lidar_in, imu_in, lidar_out, imu_out):
        self.lock=threading.Lock();self.offsets={}
        self.lidar_pub=rospy.Publisher(lidar_out,CustomMsg,queue_size=20)
        self.imu_pub=rospy.Publisher(imu_out,Imu,queue_size=200)
        rospy.Subscriber(lidar_in,CustomMsg,self.lidar,queue_size=20)
        rospy.Subscriber(imu_in,Imu,self.imu,queue_size=200)

    def stamp(self,kind,stamp):
        now=rospy.Time.now().to_sec();value=stamp.to_sec()
        with self.lock:
            if kind not in self.offsets:
                self.offsets[kind]=now-value
                rospy.loginfo('Timestamp bridge %s offset %.6fs (input %.6f -> host %.6f)',kind,self.offsets[kind],value,now)
            return rospy.Time.from_sec(value+self.offsets[kind])

    @staticmethod
    def publish(pub,msg):
        try:pub.publish(msg)
        except rospy.ROSException:pass  # ROS shutdown can close publishers before the final callback returns.

    def lidar(self,msg):
        out=copy.copy(msg);out.header=copy.copy(msg.header);out.header.stamp=self.stamp('lidar',msg.header.stamp);self.publish(self.lidar_pub,out)

    def imu(self,msg):
        out=copy.copy(msg);out.header=copy.copy(msg.header);out.header.stamp=self.stamp('imu',msg.header.stamp);self.publish(self.imu_pub,out)


if __name__=='__main__':
    if len(sys.argv)!=5:raise SystemExit('usage: timestamp_bridge.py lidar_in imu_in lidar_out imu_out')
    rospy.init_node('livo_timestamp_bridge',disable_signals=False)
    Bridge(*sys.argv[1:]);rospy.spin()
