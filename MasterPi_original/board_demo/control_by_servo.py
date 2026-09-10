#!/usr/bin/python3
# coding=utf8

# 第2章 课程基础/2.机械臂逆运动学基础课程/第3课 单次控制多个舵机 （2.Basic Lesson/2.Robotic Arm Inverse Kinematic Lesson/Lesson 3 Multiple Servos Control）

import time
import common.yaml_handle as yaml_handle
from common.ros_robot_controller_sdk import Board

board = Board()
deviation_data = yaml_handle.get_yaml_data(yaml_handle.Deviation_file_path)
board.pwm_servo_set_position(0.5, [[1, 1300]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1500]])
time.sleep(1)

board.pwm_servo_set_position(0.5, [[1, 1300], [3, 700]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1500], [3, 500]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1300], [3, 700]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1500+ deviation_data['1']], [3, 500+ deviation_data['3']]])
time.sleep(1)


board.pwm_servo_set_position(0.5, [[4, 2200]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[4, 2400+ deviation_data['4']]])
time.sleep(1)

board.pwm_servo_set_position(0.5, [[5, 580]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[5, 780+ deviation_data['5']]])
time.sleep(1)

board.pwm_servo_set_position(0.5, [[1, 1300], [6, 1300]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1500], [6, 1500]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1300], [6, 1700]])
time.sleep(1)
board.pwm_servo_set_position(0.5, [[1, 1500+ deviation_data['1']], [6, 1500+ deviation_data['6']]])