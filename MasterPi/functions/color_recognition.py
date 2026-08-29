#!/usr/bin/python3
# coding=utf8
import sys
import cv2
import time
import math
import common.misc as Misc
import threading
import common.yaml_handle as yaml_handle

if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)

range_rgb = {
    'red': (0, 0, 255),
    'blue': (255, 0, 0),
    'green': (0, 255, 0),
    'black': (0, 0, 0),
    'white': (255, 255, 255),
}

lab_data = None
def load_config():
    global lab_data, servo_data
    lab_data = yaml_handle.get_yaml_data(yaml_handle.lab_file_path)

target_color = ('red', 'green', 'blue')
def setTargetColor(target_color_):
    global target_color

    target_color = target_color_
    return (True, ())

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
            if contour_area_temp > 300:  # 只有在面积大于300时，最大面积的轮廓才是有效的，以过滤干扰(Only the contour with the largest area, which is greater than 300, is considered valid to filter out the interference.)
                area_max_contour = c

    return area_max_contour, contour_area_max  # 返回最大的轮廓(return the maximum contour)

# 夹持器夹取时闭合的角度(the closing angle of the gripper while grasping an object)
servo1 = 1500

# 初始位置(initial position)
def initMove():
    board.pwm_servo_set_position(0.3, [[1, servo1]])
    AK.setPitchRangeMoving((0, 5, 18), 0,-90, 90,1500)
   
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
        
color_list = []
__isRunning = False
detect_color = 'None'
size = (640, 480)
interval_time = 0
draw_color = range_rgb["black"]


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

def run(img):
    global interval_time
    global __isRunning, color_list
    global detect_color, draw_color
    
    if not __isRunning:  # 检测是否开启玩法，没有开启则返回原图像(detect if the game is enabled, if not, return the original image)
        return img
    else:
        img_copy = img.copy()
        img_h, img_w = img.shape[:2]
        
        frame_resize = cv2.resize(img_copy, size, interpolation=cv2.INTER_NEAREST)
        frame_gb = cv2.GaussianBlur(frame_resize, (3, 3), 3)
        
        frame_lab = cv2.cvtColor(frame_gb, cv2.COLOR_BGR2LAB)  # 将图像转换到LAB空间(convert the image to LAB space)

        color_area_max = None
        max_area = 0
        areaMaxContour_max = 0
        for i in lab_data:
            if i in target_color:
                frame_mask = cv2.inRange(frame_lab,
                                             (lab_data[i]['min'][0],
                                              lab_data[i]['min'][1],
                                              lab_data[i]['min'][2]),
                                             (lab_data[i]['max'][0],
                                              lab_data[i]['max'][1],
                                              lab_data[i]['max'][2]))  #对原图像和掩模进行位运算(perform bitwise operation on the original image and the mask)
                opened = cv2.morphologyEx(frame_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # 开运算(opening operation)
                closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))  # 闭运算(closing operation)
                contours = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[-2]  # 找出轮廓(find contours)
                areaMaxContour, area_max = getAreaMaxContour(contours)  # 找出最大轮廓(find the largest contour)
                if areaMaxContour is not None:
                    if area_max > max_area:  # 找最大面积(find the maximum area)
                        max_area = area_max
                        color_area_max = i
                        areaMaxContour_max = areaMaxContour
        if max_area > 2500:  # 有找到最大面积(the largest area has been found)
            rect = cv2.minAreaRect(areaMaxContour_max)
            box = np.intp(cv2.boxPoints(rect))
            
            cv2.drawContours(img, [box], -1, range_rgb[color_area_max], 2)
            
            (center_x, center_y), radius = cv2.minEnclosingCircle(areaMaxContour_max)  # 获取最小外接圆中心坐标和半径(get the center coordinate and radius of the minimum circumscribed circle)
            center_x = int(Misc.map(center_x, 0, size[0], 0, img_w)) # 坐标和半径映射到实际显示大小(map the coordinate and the radius to actual size for displaying)
            center_y = int(Misc.map(center_y, 0, size[1], 0, img_h))
            cv2.putText(img, 'X:'+str(center_x)+' Y:'+str(center_y), (center_x-65, center_y+100 ), cv2.FONT_HERSHEY_SIMPLEX, 0.65,draw_color, 2) # 在画面显示中心坐标
            
            
            if color_area_max == 'red':  # 红色最大(red is the maximum)
                color = 1
            elif color_area_max == 'green':  # 绿色最大(green is the maximum)
                color = 2
            elif color_area_max == 'blue':  # 蓝色最大(blue is the maximum)
                color = 3
            else:
                color = 0
            color_list.append(color)
            if len(color_list) == 3:  # 多次判断(multiple judgements)
                # 取平均值(get mean)
                color = int(round(np.mean(np.array(color_list))))
                color_list = []
                
                if color == 1:
                    detect_color = 'red'
                    draw_color = range_rgb["red"]
                    #board.set_buzzer(1900,0.1,0.9,1)
                elif color == 2:
                    detect_color = 'green'
                    draw_color = range_rgb["green"]
                elif color == 3:  
                    detect_color = 'blue'
                    draw_color = range_rgb["blue"]
                else:
                    detect_color = 'None'
                    draw_color = range_rgb["black"]
        else:
            draw_color = (0, 0, 0)
            detect_color = "None"
        
        set_rgb(detect_color) # 设置扩展板上的彩灯与检测到的颜色一样(set the color of the RGB light on the expansion board to match the detected color)      
        
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

