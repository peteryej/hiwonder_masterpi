#!/usr/bin/python3
# coding=utf8
import sys
import time
import signal
import common.mecanum as mecanum

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)
    
print('''
**********************************************************
*******************功能:小车斜向移动例程(function: oblique movement)*********************
**********************************************************
----------------------------------------------------------
Official website:https://www.hiwonder.com
Online mall:https://hiwonder.tmall.com
----------------------------------------------------------
Tips:
 * 按下Ctrl+C可关闭此次程序运行，若失败请多次尝试！(Press 'Ctrl+C' to close the program. If unsuccessful, please try multiple times!)
----------------------------------------------------------
''')

chassis = mecanum.MecanumChassis()

start = True
#关闭前处理(process before closing)
def Stop(signum, frame):
    global start

    start = False
    print('关闭中...')
    chassis.set_velocity(0,0,0)  # 关闭所有电机(close all motors)
    

signal.signal(signal.SIGINT, Stop)

if __name__ == '__main__':
    while start:
        chassis.set_velocity(50,45,0) # 控制机器人移动函数,线速度50(0~100)，方向角45(0~360)，偏航角速度0(-2~2)(The function controls the movement of a robot at the linear velocity of 50 ranging from 0 to 100, the direction angle of 45 ranging from 0 to 360, and the yaw rate of 0 ranging from -2 to 2.)
        time.sleep(1)
        chassis.set_velocity(50,315,0)
        time.sleep(1)
        chassis.set_velocity(50,225,0)
        time.sleep(1)
        chassis.set_velocity(50,135,0)
        time.sleep(1)
    chassis.set_velocity(0,0,0)  # 关闭所有电机(close all motors)
    print('已关闭')

        
