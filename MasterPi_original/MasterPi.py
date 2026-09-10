#!/usr/bin/python3
# coding=utf8
import sys
import os
import cv2
import time
import queue
import Camera
import logging
import threading
import rpc_server
import mjpg_server
import numpy as np
import common.sonar as Sonar
from kinematics.arm_move_ik import *
from common.ros_robot_controller_sdk import Board

# MasterPi主程序

import functions.running as Running
import functions.avoidance as Avoidance
import functions.remote_control as RemoteControl

# 实例化逆运动学库(instantiate the inverse kinematics library)
AK = ArmIK()
board = Board()
board.enable_reception()   

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)
HWSONAR = Sonar.Sonar() #超声波传感器(ultrasonic sensor)
board.enable_reception()
QUEUE_RPC = queue.Queue(10)

voltage = 0.0

def voltageDetection():
    global voltage
    vi = 0
    dat = []
    previous_time = 0.00  
    try:
        while True:
            if time.time() >= previous_time + 1.00 :
                previous_time = time.time()
                volt = board.get_battery()
                
                print(volt)
                if volt is not None:
                    volt /= 1000.0
                    if 5.0 < volt < 8.5:
                        dat.insert(vi, volt)
                        vi = vi + 1            
                    if vi >= 3:
                        vi = 0
                        volt1 = dat[0]
                        volt2 = dat[1]
                        volt3 = dat[2]
                        voltage = (volt1+volt2+volt3)/3.0 
                        print('Voltage:','%0.2f' % voltage)
            else:
                time.sleep(0.01)
            
    except Exception as e:
        print('Error', e)
            
        
# 运行子线程(run sub-thread)
VD = threading.Thread(target=voltageDetection)
VD.daemon = True
VD.start()


def startTruckPi():
    global HWEXT, HWSONIC
    global voltage

    AK.board = board
    rpc_server.board = board
    rpc_server.AK = AK
    rpc_server.set_board()
    
    previous_time = 0.00
    # 超声波开启后默认关闭灯(After opening the ultrasonic, its light is closed by default)
    HWSONAR.setRGBMode(0)
    HWSONAR.setPixelColor(0, (0,0,0))
    HWSONAR.setPixelColor(1, (0,0,0))    
    HWSONAR.show()
    
    # 玩法调用的超声波(the ultrasonic called by the game)
    RemoteControl.HWSONAR = HWSONAR
    RemoteControl.init()
    rpc_server.HWSONAR = HWSONAR
    Avoidance.HWSONAR = HWSONAR
    
    Avoidance.board = board
    rpc_server.board = board
    rpc_server.set_board()
    
    rpc_server.QUEUE = QUEUE_RPC

    threading.Thread(target=rpc_server.startRPCServer,
                     daemon=True).start()  # rpc服务器(rpc server)
    threading.Thread(target=mjpg_server.startMjpgServer,
                     daemon=True).start()  # mjpg流服务器(mjpq stream server)
    
    loading_picture = cv2.imread('/home/pi/MasterPi/CameraCalibration/loading.jpg')
    cam = Camera.Camera()  # 相机读取(camera reading)
    cam.camera_open()
    Running.cam = cam

    while True:
        
        time.sleep(0.03)
        # 执行需要在本线程中执行的RPC命令(execute the RPC command to be operated in this thread)
        while True:
            try:
                req, ret = QUEUE_RPC.get(False)
                event, params, *_ = ret
                ret[2] = req(params)  # 执行RPC命令(execute the RPC command)
                event.set()
            except:
                break
        #####
        # 执行功能玩法程序：(execute function game program)
        try:
            if Running.RunningFunc > 0 and Running.RunningFunc <= 9:
                if cam.frame is not None:
                    frame = cam.frame.copy()
                    img = Running.CurrentEXE().run(frame)
                    if Running.RunningFunc == 9:
                        mjpg_server.img_show = np.vstack((img, frame))
                    else:
                        if voltage <= 7.2: 
                            mjpg_server.img_show = cv2.putText(img, "Voltage:%.1fV"%voltage, (420, 460), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,255), 2)
                        else:
                            mjpg_server.img_show = cv2.putText(img, "Voltage:%.1fV"%voltage, (420, 460), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,255,0), 2)
                else:
                    mjpg_server.img_show = loading_picture
            else:
                mjpg_server.img_show = cam.frame
                #cam.frame = None
        except KeyboardInterrupt:
            print('RunningFunc1', Running.RunningFunc)
            break

if __name__ == '__main__':
    logging.basicConfig(level=logging.ERROR)
    startTruckPi()
