#!/usr/bin/python3
# coding=utf8
import sys
import cv2
import time
import sys
import numpy as np
import threading
import mediapipe as mp
from common import yaml_handle
from common.ros_robot_controller_sdk import Board


debug = False

iHWSONAR = None
board = None
if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)
 
# 导入人脸识别模块(import facial recognition module)
Face = mp.solutions.face_detection
# 自定义人脸识别方法，最小的人脸检测置信度0.5(Customize face recognition method, and the minimum face detection confidence is 0.5)
faceDetection = Face.FaceDetection(min_detection_confidence=0.8)

lab_data = None
servo_data = None
def load_config():
    global lab_data, servo_data
    
    lab_data = yaml_handle.get_yaml_data(yaml_handle.lab_file_path)

load_config()

# 夹持器夹取时闭合的角度(the closing angle of the gripper while grasping an object)
servo1 = 1500
x_pulse = 1500
# 初始位置(initial position)
def initMove():
    board.pwm_servo_set_position(0.3, [[1, servo1]])
    AK.setPitchRangeMoving((0, 6, 18), 0,-90, 90,1500)
    
d_pulse = 20
start_greet = False
action_finish = True
# 变量重置(reset variables)
def reset():
    global d_pulse
    global start_greet
    global x_pulse    
    global action_finish

 
    start_greet = False
    action_finish = True
    x_pulse = 500 
    init_move()  

__isRunning = False

# 初始化调用(call the initialization of the app)
def init():
    print("ColorDetect Init")
    load_config()
    initMove()


# 开始玩法调用(the app starts the game calling)
def start():
    global __isRunning
    __isRunning = True
    print("ColorDetect Start")

def move():
    global start_greet
    global action_finish
    global d_pulse, x_pulse    

    
    while True:
        if __isRunning:
            if start_greet:
                start_greet = False
                action_finish = False
                board.pwm_servo_set_position(0.05, [[3, 500]])
                time.sleep(0.4)
                board.pwm_servo_set_position(0.05, [[3, 900]])  
                time.sleep(0.4)
                board.pwm_servo_set_position(0.05, [[3, 500]])
                time.sleep(0.4)
                board.pwm_servo_set_position(0.05, [[3, 700]])  
                action_finish = True
                time.sleep(0.5)
            else:
                if x_pulse >= 1900 or x_pulse <= 1100:
                    d_pulse = -d_pulse
            
                x_pulse += d_pulse
                
                board.pwm_servo_set_position(0.05, [[6, x_pulse]])    
                time.sleep(0.05)
        else:
            time.sleep(0.01)

# 运行子线程(run sub-thread)
threading.Thread(target=move, args=(), daemon=True).start()


size = (320, 240)
def run(img):
    global __isRunning, area
    global center_x, center_y
    global center_x, center_y, area
    global start_greet
    global action_finish
    if not __isRunning:   # 检测是否开启玩法，没有开启则返回原图像(Detect if the game is started, if not, return the original image)
        return img
    
    img_copy = img.copy()
    img_h, img_w = img.shape[:2]
     
    imgRGB = cv2.cvtColor(img_copy, cv2.COLOR_BGR2RGB) # 将BGR图像转为RGB图像(convert BGR image to RGB image)
    results = faceDetection.process(imgRGB) # 将每一帧图像传给人脸识别模块(transmit the image of each frame to facial recognition module)

    if results.detections:  # 如果检测不到人脸那就返回None(If no face is detected, return None)

        for index, detection in enumerate(results.detections):  # 返回人脸索引index(第几张脸)，和关键点的坐标信息(Return the face index (which face) and the coordinate information of the keypoints)
            scores = list(detection.score)
            if scores and scores[0] > 0.7:
                bboxC = detection.location_data.relative_bounding_box  # 设置一个边界框，接收所有的框的xywh及关键点信息(Set a bounding box to receive xywh and keypoint information for all received boxes)
                
                # 将边界框的坐标点,宽,高从比例坐标转换成像素坐标(Convert the coordinates' width and height of the bounding box from proportional coordinates to pixel coordinates)
                bbox = (
                    int(bboxC.xmin * img_w),
                    int(bboxC.ymin * img_h),
                    int(bboxC.width * img_w),
                    int(bboxC.height * img_h)
                )
                
                cv2.rectangle(img, bbox, (0, 255, 0), 2)  # 在每一帧图像上绘制矩形框(draw a rectangle on each frame of the image)
                
                # 获取识别框的信息, xy为左上角坐标点(Get information about the recognition box, where xy is the coordinates of the upper left corner)
                x, y, w, h = bbox
                center_x = int(x + (w / 2))
                center_y = int(y + (h / 2))
                area = int(w * h)
                if action_finish:
                    start_greet = True


    else:
        center_x, center_y, area = -1, -1, 0
            
    return img

if __name__ == '__main__':
    from kinematics.arm_move_ik import *
    from common.ros_robot_controller_sdk import Board
    board = Board()
    # 实例化逆运动学库(instantiate the inverse kinematics library)
    AK = ArmIK()
    AK.board = board
    
    init()
    start()
    cap = cv2.VideoCapture('http://127.0.0.1:8080?action=stream')
    #cap = cv2.VideoCapture(0)
    while True:
        ret,img = cap.read()
        if ret:
            frame = img.copy()
            Frame = run(frame)  
            frame_resize = cv2.resize(Frame, (320, 240))
            cv2.imshow('frame', frame_resize)
            key = cv2.waitKey(1)
            if key == 27:
                break
        else:
            time.sleep(0.01)
    my_camera.camera_close()
    cv2.destroyAllWindows()