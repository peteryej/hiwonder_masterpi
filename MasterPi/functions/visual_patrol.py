#!/usr/bin/python3
# coding=utf8
import sys
sys.path.append('/home/pi/MasterPi/')
import cv2
import time
import math
import signal
import Camera
import threading
import numpy as np
import common.pid as PID
import common.misc as Misc
import common.yaml_handle as yaml_handle
import common.mecanum as mecanum
from kinematics.transform import *

#  3.进阶课程/1.AI视觉玩法课程/第4课 智能巡线(3.Advanced Lesson/1.AI Vision Games Lesson/Lesson 4 Line Following)

chassis = mecanum.MecanumChassis()
pitch_pid = PID.PID(P=0.001, I=0.00001, D=0.000001)

range_rgb = {
    'red': (0, 0, 255),
    'blue': (255, 0, 0),
    'green': (0, 255, 0),
    'black': (0, 0, 0),
    'white': (255, 255, 255),
}

# 巡线(line following)
if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)


# 设置检测颜色(set target color)
def setTargetColor(target_color):
    global __target_color

    print("COLOR", target_color)
    __target_color = target_color
    return (True, ())

lab_data = None

def load_config():
    global lab_data
    lab_data = yaml_handle.get_yaml_data(yaml_handle.lab_file_path)

# 初始位置(initial position)
def initMove():
    board.pwm_servo_set_position(0.8, [[1, 1500]])
    AK.setPitchRangeMoving((0, 7, 11), -60, -90, 0, 1500)
    chassis.set_velocity(0,90,0)  # 关闭所有电机(close all motors)

    
line_centerx = -1
# 变量重置(reset variables)
def reset():
    global line_centerx
    global __target_color
    
    line_centerx = -1
    __target_color = ()
    
# app初始化调用(call the initialization of the app)
def init():
    print("VisualPatrol Init")
    load_config()
    initMove()

__isRunning = False
# app开始玩法调用(the app starts the game calling)
def start():
    global __isRunning
    reset()
    __isRunning = True
    print("VisualPatrol Start")

# app停止玩法调用(the app stops the game calling)
def stop():
    global __isRunning
    __isRunning = False
    chassis.set_velocity(0,90,0)  # 关闭所有电机(close all motors)
    print("VisualPatrol Stop")

# app退出玩法调用(the app exits the game calling)
def exit():
    global __isRunning
    __isRunning = False
    chassis.set_velocity(0,90,0)  # 关闭所有电机(close all motors)
    print("VisualPatrol Exit")
    
# 找出面积最大的轮廓(find the contour with the largest area)
# 参数为要比较的轮廓的列表(the parameter is the listing of contours to be compared)
def getAreaMaxContour(contours):
    contour_area_temp = 0
    contour_area_max = 0
    area_max_contour = None

    for c in contours:  # 历遍所有轮廓(iterate through all contours)
        contour_area_temp = math.fabs(cv2.contourArea(c))  # 计算轮廓面积(calculate contour area)
        if contour_area_temp > contour_area_max:
            contour_area_max = contour_area_temp
            if contour_area_temp >= 5:  # 只有在面积大于300时，最大面积的轮廓才是有效的，以过滤干扰(Only the contour with the largest area, which is greater than 300, is considered valid to filter out the interference.)
                area_max_contour = c

    return area_max_contour, contour_area_max  # 返回最大的轮廓(return the maximum contour)

img_centerx = 320
line_centerx = -1
def move():
    global line_centerx

    i = 0
    while True:
        if __isRunning:
            if line_centerx > 0:
                # 计算线的中心点和画面中心点的值(calculate values of the center points of the line and the image)
                num = line_centerx - img_centerx
                # 偏差较小，不进行处理(the deviation is small, and no processing is necessary)
                if abs(num)< 25:
                    pitch_pid.SetPoint = num
                else:
                    pitch_pid.SetPoint = 0
                pitch_pid.update(num) 
                angle = pitch_pid.output # 获取PID输出值(obtain the output value of the PID)
                print('angle:',angle)
                    
                chassis.set_velocity(50, 90, angle)
                time.sleep(1)
                
            else :
                chassis.set_velocity(0,90,0)  # 关闭所有电机(close all motors)
                time.sleep(0.01)
        else:
            time.sleep(0.01)
 
# 运行子线程(run a sub-thread)
th = threading.Thread(target=move)
th.daemon = True
th.start()

roi = [ # [ROI, weight]
        (240, 280,  0, 640, 0.1), 
        (340, 380,  0, 640, 0.3), 
        (430, 460,  0, 640, 0.6)
       ]

roi_h1 = roi[0][0]
roi_h2 = roi[1][0] - roi[0][0]
roi_h3 = roi[2][0] - roi[1][0]

roi_h_list = [roi_h1, roi_h2, roi_h3]

size = (640, 480)
def run(img):
    global line_centerx
    global __target_color
    
    img_copy = img.copy()
    img_h, img_w = img.shape[:2]
    
    if not __isRunning or __target_color == ():
        return img
     
    frame_resize = cv2.resize(img_copy, size, interpolation=cv2.INTER_NEAREST)
    frame_gb = cv2.GaussianBlur(frame_resize, (3, 3), 3)         
    centroid_x_sum = 0
    weight_sum = 0
    center_ = []
    n = 0

    #将图像分割成上中下三个部分，这样处理速度会更快，更精确(Devide the image into three parts: top, middle, and bottom. This allows faster and more accurate processing.)
    for r in roi:
        roi_h = roi_h_list[n]
        n += 1       
        blobs = frame_gb[r[0]:r[1], r[2]:r[3]]
        frame_lab = cv2.cvtColor(blobs, cv2.COLOR_BGR2LAB)  # 将图像转换到LAB空间(convert the image to LAB space)
        area_max = 0
        areaMaxContour = 0
        for i in lab_data:
            if i in __target_color:
                detect_color = i
                frame_mask = cv2.inRange(frame_lab,
                                         (lab_data[i]['min'][0],
                                          lab_data[i]['min'][1],
                                          lab_data[i]['min'][2]),
                                         (lab_data[i]['max'][0],
                                          lab_data[i]['max'][1],
                                          lab_data[i]['max'][2]))  #对原图像和掩模进行位运算(perform bitwise operation on the original image and the mask)
                eroded = cv2.erode(frame_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))  #腐蚀(erode)
                dilated = cv2.dilate(eroded, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))) #膨胀(dilate)

        cnts = cv2.findContours(dilated , cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_TC89_L1)[-2]#找出所有轮廓(find all contours)
        cnt_large, area = getAreaMaxContour(cnts)#找到最大面积的轮廓(find the contour with the largest area)
        if cnt_large is not None:#如果轮廓不为空(if the contour is not empty)
            rect = cv2.minAreaRect(cnt_large)#最小外接矩形(the minimum bounding rectangle)
            box = np.intp(cv2.boxPoints(rect))#最小外接矩形的四个顶点(four vertex the minimum bounding rectangle)
            for i in range(4):
                box[i, 1] = box[i, 1] + (n - 1)*roi_h + roi[0][0]
                box[i, 1] = int(Misc.map(box[i, 1], 0, size[1], 0, img_h))
            for i in range(4):                
                box[i, 0] = int(Misc.map(box[i, 0], 0, size[0], 0, img_w))

            cv2.drawContours(img, [box], -1, (0,0,255,255), 2)#画出四个点组成的矩形(draw a rectangle composed of four points)
        
            #获取矩形的对角点(obtain the diagonal point of the rectangle)
            pt1_x, pt1_y = box[0, 0], box[0, 1]
            pt3_x, pt3_y = box[2, 0], box[2, 1]            
            center_x, center_y = (pt1_x + pt3_x) / 2, (pt1_y + pt3_y) / 2#中心点(ceter point)
            cv2.circle(img, (int(center_x), int(center_y)), 5, (0,0,255), -1)#画出中心点(draw the center point)
            center_.append([center_x, center_y])                        
            #按权重不同对上中下三个中心点进行求和(sum the three center points of the top, middle, and bottom based on different weights)
            centroid_x_sum += center_x * r[4]
            weight_sum += r[4]
    if weight_sum != 0:
        #求最终得到的中心点(calculate the final center point)
        cv2.circle(img, (line_centerx, int(center_y)), 10, (0,255,255), -1)#画出中心点(draw the center point)
        line_centerx = int(centroid_x_sum / weight_sum)  
    else:
        line_centerx = -1
    return img

#关闭前处理(process before closing)
def Stop(signum, frame):
    global __isRunning
    
    __isRunning = False
    print('关闭中...')
    chassis.set_velocity(0,90,0)  # 关闭所有电机(close all motors)

if __name__ == '__main__':
    from kinematics.arm_move_ik import *
    from common.ros_robot_controller_sdk import Board
    board = Board()
    # 实例化逆运动学库(instantiate the inverse kinematics library)
    AK = ArmIK()
    AK.board = board    

    init()
    start()
    signal.signal(signal.SIGINT, Stop)
    cap = cv2.VideoCapture('http://127.0.0.1:8080?action=stream')
    __target_color = ('black',)
    while __isRunning:
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
    cv2.destroyAllWindows()
