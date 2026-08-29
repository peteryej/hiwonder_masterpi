#!/usr/bin/python3
# coding=utf8
import sys
import cv2
import time
sys.path.append('/home/pi/MasterPi/')
import Camera
import threading
import common.sonar as Sonar
import common.yaml_handle as yaml_handle
from kinematics.transform import *
from CameraCalibration.CalibrationConfig import *

#  3.进阶课程/1.AI视觉玩法课程/第2课 颜色分拣(3.Advanced Lesson/1.AI Vision Games Lesson/Lesson 2 Color Sorting)

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)

HWSONAR = Sonar.Sonar() #超声波传感器(ultrasonic sensor)

range_rgb = {
    'red':   (0, 0, 255),
    'blue':  (255, 0, 0),
    'green': (0, 255, 0),
    'black': (0, 0, 0),
    'white': (255, 255, 255),
}

lab_data = None
def load_config():
    global lab_data, servo_data
    
    lab_data = yaml_handle.get_yaml_data(yaml_handle.lab_file_path)

__target_color = ('red')
# 设置检测颜色(set the target color)
def setTargetColor(target_color):
    global __target_color

    print("COLOR", target_color)
    __target_color = target_color
    return (True, ())

#找出面积最大的轮廓(find the contour with the largest area)
#参数为要比较的轮廓的列表(the parameter is the listing of contours to be compared)
def getAreaMaxContour(contours) :
        contour_area_temp = 0
        contour_area_max = 0
        area_max_contour = None

        for c in contours : #历遍所有轮廓(iterate through all contours)
            contour_area_temp = math.fabs(cv2.contourArea(c))  #计算轮廓面积(calculate contour area)
            if contour_area_temp > contour_area_max:
                contour_area_max = contour_area_temp
                if contour_area_temp > 300:  #只有在面积大于300时，最大面积的轮廓才是有效的，以过滤干扰(Only the contour with the largest area, which is greater than 300, is considered valid to filter out the interference.)
                    area_max_contour = c

        return area_max_contour, contour_area_max  #返回最大的轮廓(return maximum contour)

# 夹持器夹取时闭合的角度(the closing angle of the gripper while grasping an object)
servo1 = 1500

# 初始位置(initial position)
def initMove():
    board.pwm_servo_set_position(0.3, [[1, servo1]])
    AK.setPitchRangeMoving((0, 8, 10), -90,-90, 0,1500)

#设置扩展板的RGB灯颜色使其跟要追踪的颜色一致(set the color of the RGB light on the expansion board to match the color to be tracked.)
def set_rgb(color):
    if color == "red":
        board.set_rgb([[1, 255, 0, 0], [2, 255, 0, 0]])
    elif color == "green":
        board.set_rgb([[1, 0, 255, 0], [2, 0, 255, 0]])
    elif color == "blue":
        board.set_rgb([[1, 0, 0, 255], [2, 0, 0, 255]])
    else:
        board.set_rgb([[1, 0, 0, 0], [2, 0, 0, 0]])

_stop = False
color_list = []
__isRunning = False
detect_color = 'None'
start_pick_up = False
start_count_t1 = True

# 变量重置(reset variables)
def reset():
    global _stop
    global color_list
    global detect_color
    global start_pick_up
    global __target_color
    global start_count_t1

    _stop = False
    color_list = []
    __target_color = ()
    detect_color = 'None'
    start_pick_up = False
    start_count_t1 = True

# app初始化调用(call the initialization of the app)
def init():
    print("ColorSorting Init")
    # 超声波开启后默认关闭灯(After enabling the ultrasonic sensor, its light is turned off by default.)
    HWSONAR.setRGBMode(0)
    HWSONAR.setPixelColor(0, (0,0,0))
    HWSONAR.setPixelColor(1, (0,0,0))    
    HWSONAR.show()
    load_config()
    initMove()

# app开始玩法调用(the app starts the game calling)
def start():
    global __isRunning
    reset()
    __isRunning = True
    print("ColorSorting Start")

# app停止玩法调用(the app stops the game calling)
def stop():
    global _stop
    global __isRunning
    _stop = True
    __isRunning = False
    set_rgb('None')
    print("ColorSorting Stop")

# app退出玩法调用(the app exits the game calling)
def exit():
    global _stop
    global __isRunning
    _stop = True
    set_rgb('None')
    __isRunning = False
    print("ColorSorting Exit")


rect = None
size = (640, 480)
unreachable = False 
def move():
    global rect
    global _stop
    global unreachable
    global __isRunning
    global detect_color
    global start_pick_up
    
    #放置坐标(place coordinate)
    coordinate = {
        'red':   (-15, 14, 2),
        'green': (-18, 9,  3),
        'blue':  (-18, 0, 2),
        'capture': (0, 16.5, 2)
    }
    
    while True:
        if __isRunning:        
            if detect_color != 'None' and start_pick_up:  #如果检测到方块,开始夹取(If a block is detected, it starts to grasp.)
                
                set_rgb(detect_color) # 设置扩展板上的彩灯与检测到的颜色一样(set the color of the RGB light on the expansion board to match the detected color)
                board.set_buzzer(1900, 0.1, 0.9, 1)# 设置蜂鸣器响0.1秒 (set the buzzer to sound for 0.1s)
                
                AK.setPitchRangeMoving((0, 6, 18), 0,-90, 90, 1500) # 机械臂抬起来(lift the robotic arm)
                time.sleep(1.5)
                if not __isRunning:  # 检测是否停止玩法(detect if the game is stopped)
                    continue
                board.pwm_servo_set_position(0.5, [[1, 2000]])# 张开爪子(open the gripper)
                time.sleep(1.5)
                if not __isRunning:
                    continue
                board.pwm_servo_set_position(0.5, [[1, 1500]])# 闭合爪子(close the gripper)
                time.sleep(1.5)
                if not __isRunning:
                    continue
                if detect_color == 'red':       # 根据检测到的颜色，机械臂转动到对应角度(According to the detected color, the robotic arm rotates to the corresponding angle.)
                    board.pwm_servo_set_position(0.5, [[6, 1900]])
                    time.sleep(0.5)
                elif detect_color == 'green':
                    board.pwm_servo_set_position(0.8, [[6, 2100]])
                    time.sleep(0.8)
                elif detect_color == 'blue':
                    board.pwm_servo_set_position(1.5, [[6, 2500]])
                    time.sleep(1.5)
                if not __isRunning:
                    continue
                result = AK.setPitchRangeMoving((coordinate[detect_color][0], coordinate[detect_color][1], 8), -90, -90, 0,800) # 运行到对应颜色的坐标上方(move above the coordinates of the corresponding color)
                if result == False:
                    unreachable = True
                else:
                    unreachable = False
                    time.sleep(result[2]/1000) #如果可以到达指定位置，则获取运行时间(If the specified place can be reached, it obtains the runtime.)
                if not __isRunning:
                    continue
                AK.setPitchRangeMoving((coordinate[detect_color]), -90, -90, 0, 500)  # 放置到检测到颜色对应的坐标(place to the corresponding coordinate of the detected color)
                time.sleep(0.5)
                if not __isRunning:
                    continue
                board.pwm_servo_set_position(0.5, [[1, 1800]])# 张开爪子(open the gripper)
                time.sleep(0.5)
                if not __isRunning:
                    continue
                AK.setPitchRangeMoving((coordinate[detect_color][0], coordinate[detect_color][1], 8), -90, -90, 0, 800) # 运行到对应颜色的坐标上方(move above the coordinates of the corresponding color)
                time.sleep(0.8)
                if not __isRunning:
                    continue
                board.pwm_servo_set_position(1.2, [[1, 1500],[3, 515],[4, 2170],[5, 945]])# 机械臂进行复位(reset the robotic arm)
                time.sleep(1.2)
                if detect_color == 'red':
                    board.pwm_servo_set_position(0.5, [[6, 1500]])
                    time.sleep(0.5)
                elif detect_color == 'green':
                    board.pwm_servo_set_position(0.8, [[6, 1500]])
                    time.sleep(0.8)
                elif detect_color == 'blue':
                    board.pwm_servo_set_position(1.5, [[6, 1500]])
                    time.sleep(1.5)
                AK.setPitchRangeMoving((0, 8, 10), -90, -90, 0, 1000)
                time.sleep(1)
                
                detect_color = 'None'
                start_pick_up = False
                set_rgb(detect_color)
            else:
                time.sleep(0.01)                
        else:
            if _stop:
                _stop = False
                initMove()
            time.sleep(0.01)
          
#运行子线程(run the sub-thread)
th = threading.Thread(target=move)
th.daemon = True
th.start()    

draw_color = range_rgb["black"]
def run(img):
    global rect
    global unreachable
    global __isRunning
    global start_pick_up
    global start_count_t1
    global detect_color, draw_color, color_list
    
    img_copy = img.copy()
    img_h, img_w = img.shape[:2]   

    if not __isRunning: # 检测是否开启玩法，没有开启则返回原图像(detect if the game is enabled, if not, return the original image)
        return img
    
    frame_resize = cv2.resize(img_copy, size, interpolation=cv2.INTER_NEAREST)
    frame_gb = cv2.GaussianBlur(frame_resize, (3, 3), 3)     
    frame_lab = cv2.cvtColor(frame_gb, cv2.COLOR_BGR2LAB)  # 将图像转换到LAB空间(convert the image to LAB space)

    color_area_max = None
    max_area = 0
    areaMaxContour_max = 0
    
    if not start_pick_up:
        for i in lab_data:
            if i in __target_color:
                frame_mask = cv2.inRange(frame_lab,
                                             (lab_data[i]['min'][0],
                                              lab_data[i]['min'][1],
                                              lab_data[i]['min'][2]),
                                             (lab_data[i]['max'][0],
                                              lab_data[i]['max'][1],
                                              lab_data[i]['max'][2]))  #对原图像和掩模进行位运算(perform bitwise operation on the original image and the mask)
                opened = cv2.morphologyEx(frame_mask, cv2.MORPH_OPEN, np.ones((3, 3),np.uint8))  #开运算(opening operation)
                closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, np.ones((3, 3),np.uint8)) #闭运算(closing operation)
                closed[:, 0:100] = 0
                contours = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[-2]  #找出轮廓(find contours)
                areaMaxContour, area_max = getAreaMaxContour(contours)  #找出最大轮廓(find the largest contour)
                if areaMaxContour is not None:
                    if area_max > max_area:#找最大面积(find the largest area)
                        max_area = area_max
                        color_area_max = i
                        areaMaxContour_max = areaMaxContour
        if max_area > 2500:  # 有找到最大面积(the largest area has been found)
            rect = cv2.minAreaRect(areaMaxContour_max)
            box = np.intp(cv2.boxPoints(rect))
            cv2.drawContours(img, [box], -1, range_rgb[color_area_max], 2)
            
            if not start_pick_up:
                if color_area_max == 'red':  #红色最大(red is the maximum)
                    color = 1
                elif color_area_max == 'green':  #绿色最大(green is the maximum)
                    color = 2
                elif color_area_max == 'blue':  #蓝色最大(blue is the maximum)
                    color = 3
                else:
                    color = 0
                color_list.append(color)

                if len(color_list) == 3:  #多次判断(multiple judgements)
                    # 取平均值(get mean)
                    color = int(round(np.mean(np.array(color_list))))
                    color_list = []
                    if color:
                        start_pick_up = True
                        if color == 1:
                            detect_color = 'red'
                            draw_color = range_rgb["red"]
                        elif color == 2:
                            detect_color = 'green'
                            draw_color = range_rgb["green"]
                        elif color == 3:
                            detect_color = 'blue'
                            draw_color = range_rgb["blue"]
                    else:
                        start_pick_up = False
                        detect_color = 'None'
                        draw_color = range_rgb["black"]
        else:
            if not start_pick_up:
                draw_color = (0, 0, 0)
                detect_color = "None"

    cv2.putText(img, "Color: " + detect_color, (10, img.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.65, draw_color, 2)
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
    __target_color = ('red', 'green', 'blue')
    cap = cv2.VideoCapture('http://127.0.0.1:8080?action=stream')
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
    cv2.destroyAllWindows()