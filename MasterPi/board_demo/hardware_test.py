#!/usr/bin/python3
# coding=utf8
import sys
import time
from pathlib import Path

MASTERPI_ROOT = Path(__file__).resolve().parents[1]
COMMON_SDK_ROOT = MASTERPI_ROOT / 'masterpi_sdk' / 'common_sdk'
sys.path.insert(0, str(COMMON_SDK_ROOT))

import common.yaml_handle as yaml_handle
from common.ros_robot_controller_sdk import Board

board = Board()
deviation_data = yaml_handle.get_yaml_data(MASTERPI_ROOT / 'Deviation.yaml')

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)
    
print('''
**********************************************************
********************PWM舵机和电机测试(PWM servo and motor testing)************************
**********************************************************
----------------------------------------------------------
Official website:https://www.hiwonder.com
Online mall:https://hiwonder.tmall.com
----------------------------------------------------------
Tips:
 * 按下Ctrl+C可关闭此次程序运行，若失败请多次尝试！(Press 'Ctrl+C' to quit. If it fails, please try again multiple times!)
----------------------------------------------------------
''')
board.pwm_servo_set_position(0.3, [[1, 1800]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[1, 1500]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[1, 1200]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[1, 1500+ deviation_data['1']]]) 
time.sleep(1.5)

board.pwm_servo_set_position(0.3, [[3, 900]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[3, 695]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[3, 500]])
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[3, 695+ deviation_data['3']]]) 
time.sleep(1.5)

board.pwm_servo_set_position(0.3, [[4, 2215]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[4, 2415]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[4, 2215]])
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[4, 2415+ deviation_data['4']]]) 
time.sleep(1.5)

board.pwm_servo_set_position(0.3, [[5, 580]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[5, 780]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[5, 980]])
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[5, 780+ deviation_data['5']]]) 
time.sleep(1.5)

board.pwm_servo_set_position(0.3, [[6, 1800]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[6, 1500]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[6, 1200]]) 
time.sleep(0.3)
board.pwm_servo_set_position(0.3, [[6, 1500+ deviation_data['6']]]) 
time.sleep(1.5)


board.set_motor_duty([[1, -45]])
time.sleep(0.5)
board.set_motor_duty([[1, 0]])
time.sleep(1)
        
board.set_motor_duty([[2, 45]])
time.sleep(0.5)
board.set_motor_duty([[2, 0]])
time.sleep(1)

board.set_motor_duty([[3, -45]])
time.sleep(0.5)
board.set_motor_duty([[3, 0]])
time.sleep(1)

board.set_motor_duty([[4, 45]])
time.sleep(0.5)
board.set_motor_duty([[4, 0]])
time.sleep(1)
