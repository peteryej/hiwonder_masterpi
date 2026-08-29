#!/usr/bin/env python3
# encoding:utf-8
import sys
import time
from kinematics.arm_move_ik import *
from common.ros_robot_controller_sdk import Board

# 第2章 课程基础/2.机械臂逆运动学基础课程/第4课 机械臂上下左右移动（2.Basic Lesson/2.Robotic Arm Inverse Kinematic Lesson/Lesson 4 Control the Movement of Robotic Arm）

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)
    
# 实例化逆运动学库(Instantiate the inverse kinematics library)
AK = ArmIK()
AK.board = Board()
 
if __name__ == "__main__":
    '''
    AK.setPitchRangeMoving(coordinate_data, alpha, alpha1, alpha2, movetime):
    给定坐标coordinate_data和俯仰角alpha,以及俯仰角范围的范围alpha1, alpha2，自动寻找最接近给定俯仰角的解，并转到目标位置
    (Give the coordinate 'coordinate_data', the pitch angle 'alpha', and the range of the pitch angle 'alpha1' and 'alpha2'. Automatically search for the solution closest to the given pitch angle and move to the target position.)
    如果无解返回False,否则返回舵机角度、俯仰角、运行时间
    (If there is no solution, return 'False'. Otherwise, return the servo angle, pitch angle, and runtime.)
    坐标单位cm， 以元组形式传入，例如(0, 5, 10)
    (The coordinate unit is in cm and should be passed in tuple form, for example: (0, 5, 10).)
    alpha: 为给定俯仰角 (pitch angle)
    alpha1和alpha2: 为俯仰角的取值范围 (ranges of the pitch angle)
    movetime:为舵机转动时间，单位ms, 如果不给出时间，则自动计算(The servo rotation time is in ms. If the time is not specified, it will be calculated automatically.)    
    '''
    # 设置机械臂初始位置(x:0, y:6, z:18),运行时间:1500毫秒（Set the initial position of the robotic arm to x:0, y:6, z:18 and run for 1500 milliseconds."）
    AK.setPitchRangeMoving((0, 6, 18), 0,-90, 90, 1500) 
    time.sleep(1.5) # 延时1.5秒(Delay for 1.5 seconds.)

    AK.setPitchRangeMoving((5, 6, 18), 0,-90, 90, 1000)  # 设置机械臂X轴右移,运行时间:1000毫秒(Set the robotic arm to move right on the X-axis and run for 1000 milliseconds.)
    time.sleep(1.2) # 延时1.2秒 (Delay for 1.2 seconds.)
    AK.setPitchRangeMoving((5, 13, 11), 0,-90, 90, 1000) #设置机械臂Y轴、Z轴同时移动，运行时间:1000毫秒(Set the robotic arm to move simultaneously on the Y-axis and Y-axis, and run for 1000 milliseconds.)
    time.sleep(1.2) # 延时1.2秒(Delay for 1.2 seconds.)
    AK.setPitchRangeMoving((-5, 13, 11), 0,-90, 90, 1000) # 设置机械臂X轴右移,运行时间:1000毫秒(Set the robotic arm to move right on the X-axis and run for 1000 milliseconds.)
    time.sleep(1.2) # 延时1.2秒(Delay for 1.2 seconds.)
    AK.setPitchRangeMoving((-5, 6, 18), 0,-90, 90, 1000)  #设置机械臂Y轴、Z轴同时移动，运行时间:1000毫秒(Set the robotic arm to move simultaneously on the Y-axis and Y-axis, and run for 1000 milliseconds.)
    time.sleep(1.2) # 延时1.2秒(Delay for 1.2 seconds.)
    AK.setPitchRangeMoving((0, 6, 18), 0,-90, 90, 1000) # 设置机械臂X轴左移,运行时间:1000毫秒(Set the robotic arm to move left on the X-axis and run for 1000 milliseconds.)
    time.sleep(1.2) # 延时1.2秒(Delay for 1.2 seconds.)
