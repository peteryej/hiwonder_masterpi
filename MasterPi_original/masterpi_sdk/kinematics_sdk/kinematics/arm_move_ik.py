#!/usr/bin/env python3
# encoding:utf-8
import numpy as np
from kinematics.inversekinematics import *
import common.yaml_handle as yaml_handle

# 获取机械臂舵机偏差(obtain the robotic arm's servo deviation)
deviation_data = yaml_handle.get_yaml_data(yaml_handle.Deviation_file_path)

#机械臂根据逆运动学算出的角度进行移动(robotic arm moves based on the angles calculated by inverse kinematics)
ik = IK('arm')
#设置连杆长度(set the length of the linkages)
l1 = ik.l1
l4 = ik.l4
ik.setLinkLength(L1=l1+1.3, L4=l4)

class ArmIK:
    servo3Range = (500, 2500.0, 0, 180.0) #脉宽， 角度(pulse width, angle)
    servo4Range = (500, 2500.0, 0, 180.0)
    servo5Range = (500, 2500.0, 0, 180.0)
    servo6Range = (500, 2500.0, 0, 180.0)

    def __init__(self):
        self.setServoRange()

    def setServoRange(self, servo3_Range=servo3Range, servo4_Range=servo4Range, servo5_Range=servo5Range, servo6_Range=servo6Range):
        # 适配不同的舵机(adapt to different servos)
        self.servo3Range = servo3_Range
        self.servo4Range = servo4_Range
        self.servo5Range = servo5_Range
        self.servo6Range = servo6_Range
        self.servo3Param = (self.servo3Range[1] - self.servo3Range[0]) / (self.servo3Range[3] - self.servo3Range[2])
        self.servo4Param = (self.servo4Range[1] - self.servo4Range[0]) / (self.servo4Range[3] - self.servo4Range[2])
        self.servo5Param = (self.servo5Range[1] - self.servo5Range[0]) / (self.servo5Range[3] - self.servo5Range[2])
        self.servo6Param = (self.servo6Range[1] - self.servo6Range[0]) / (self.servo6Range[3] - self.servo6Range[2])

    def transformAngelAdaptArm(self, theta3, theta4, theta5, theta6):
        #将逆运动学算出的角度转换为舵机对应的脉宽值(convert the angles calculated by inverse kinematics into corresponding pulse width values for the servos)
        servo3 = int(round(theta3 * self.servo3Param + (self.servo3Range[1] + self.servo3Range[0])/2))
        if servo3 > self.servo3Range[1] or servo3 < self.servo3Range[0]:
            logger.info('servo3(%s)超出范围(%s, %s)', servo3, self.servo3Range[0], self.servo3Range[1])
            return False

        servo4 = int(round(theta4 * self.servo4Param + (self.servo4Range[1] + self.servo4Range[0])/2))
        if servo4 > self.servo4Range[1] or servo4 < self.servo4Range[0]:
            logger.info('servo4(%s)超出范围(%s, %s)', servo4, self.servo4Range[0], self.servo4Range[1])
            return False

        servo5 = int(round((self.servo5Range[1] + self.servo5Range[0])/2 + (90.0 - theta5) * self.servo5Param)) 
        if servo5 > ((self.servo5Range[1] + self.servo5Range[0])/2 + 90*self.servo5Param) or servo5 < ((self.servo5Range[1] + self.servo5Range[0])/2 - 90*self.servo5Param):
            logger.info('servo5(%s)超出范围(%s, %s)', servo5, self.servo5Range[0], self.servo5Range[1])
            return False

        if theta6 < -(self.servo6Range[3] - self.servo6Range[2])/2:
            servo6 = int(round(((self.servo6Range[3] - self.servo6Range[2])/2 + (90 + (180 + theta6))) * self.servo6Param))
        else:
            servo6 = int(round(((self.servo6Range[3] - self.servo6Range[2])/2 - (90 - theta6)) * self.servo6Param)) + self.servo6Range[0]
        if servo6 > self.servo6Range[1] or servo6 < self.servo6Range[0]:
            logger.info('servo6(%s)超出范围(%s, %s)', servo6, self.servo6Range[0], self.servo6Range[1])
            return False
        return {"servo3": servo3, "servo4": servo4, "servo5": servo5, "servo6": servo6}

    def servosMove(self, servos, movetime=None):
        #驱动3,4,5,6号舵机转动(drive servos 3, 4, 5, and 6 to rotate)
        if movetime is None:
            max_d = 0
            for i in  range(0, 4):
                #d = abs(board.pwm_servo_read_position(i + 3) - servos[i])
                d = abs(deviation_data['{}'.format(i+3)])
                print(d)
                if d > max_d:
                    max_d = d
            movetime = int(max_d*1)
        self.board.pwm_servo_set_position(float(movetime)/1000.0,[[3,servos[0]+deviation_data['3']]])
        self.board.pwm_servo_set_position(float(movetime)/1000.0,[[4,servos[1]+deviation_data['4']]])
        self.board.pwm_servo_set_position(float(movetime)/1000.0,[[5,servos[2]+deviation_data['5']]])
        self.board.pwm_servo_set_position(float(movetime)/1000.0,[[6,servos[3]+deviation_data['6']]])

        return movetime

    def setPitchRange(self, coordinate_data, alpha1, alpha2, da = 1):
        #给定坐标coordinate_data和俯仰角的范围alpha1，alpha2, 自动在范围内寻找到的合适的解(Given the coordinate "coordinate_data" and the range of pitch angles "alpha1" and "alpha2", automatically find a suitable solution within the range.)
        #如果无解返回False,否则返回对应舵机角度,俯仰角(If there is no solution, return False; otherwise, return the corresponding servo angles and pitch angle.)
        #坐标单位cm， 以元组形式传入，例如(0, 5, 10)(The coordinate unit is cm and is passed in the form of a tuple, for example, (0, 5, 10).)
        #da为俯仰角遍历时每次增加的角度("da" is the angle increment for each iteration when traversing the pitch angle range.)
        x, y, z = coordinate_data
        if alpha1 >= alpha2:
            da = -da
        for alpha in np.arange(alpha1, alpha2, da):#遍历求解(Traversal solving)
            result = ik.getRotationAngle((x, y, z), alpha)
            if result:
                theta3, theta4, theta5, theta6 = result['theta3'], result['theta4'], result['theta5'], result['theta6']               
                servos = self.transformAngelAdaptArm(theta3, theta4, theta5, theta6)
                if servos != False:
                    return servos, alpha

        return False

    def setPitchRangeMoving(self, coordinate_data, alpha, alpha1, alpha2, movetime = None):
        #给定坐标coordinate_data和俯仰角alpha,以及俯仰角范围的范围alpha1, alpha2，自动寻找最接近给定俯仰角的解，并转到目标位置(Given the coordinate "coordinate_data" and pitch angle "alpha", as well as the range of pitch angle "alpha1" and "alpha2", automatically find the solution closest to the given pitch angle and move to the target position.)
        #如果无解返回False,否则返回舵机角度、俯仰角、运行时间(If there is no solution, return False; otherwise, return the servo angle, pitch angle, and running time. )
        #坐标单位cm， 以元组形式传入，例如(0, 5, 10)(The coordinate unit is cm and is passed in the form of a tuple, for example, (0, 5, 10).)
        #alpha为给定俯仰角("alpha" is the given pitch angle.)
        #alpha1和alpha2为俯仰角的取值范围("alpha1" and "alpha2" are the range of pitch angle values.)
        #movetime为舵机转动时间，单位ms, 如果不给出时间，则自动计算("movetime" is the servo rotation time in ms. If the time is not given, it will be calculated automatically.)
        x, y, z = coordinate_data
        result1 = self.setPitchRange((x, y, z), alpha, alpha1)
        result2 = self.setPitchRange((x, y, z), alpha, alpha2)
        if result1 != False:
            data = result1
            if result2 != False:
                if abs(result2[1] - alpha) < abs(result1[1] - alpha):
                    data = result2
        else:
            if result2 != False:
                data = result2
            else:
                return False
        servos, alpha = data[0], data[1]
        movetime = self.servosMove((servos["servo3"], servos["servo4"], servos["servo5"], servos["servo6"]), movetime)
        return servos, alpha, movetime
 
if __name__ == "__main__":
    AK = ArmIK()
    #setPWMServoPulse(1, 1450, 1500)
    #AK.setPitchRangeMoving((-5,0,2), -90,-90, 90, 1200)
    #AK.setPitchRangeMoving((0, 6, 18), 0,90, 90, 500)  # 回到初始位置(return to initial position)
    print(AK.setPitchRangeMoving((0, 6, 18), 0,-90, 90, 1500))
    #board.pwm_servo_set_position(1,[[6,1418]])
#     print(ik.getLinkLength())
    #print(AK.setPitchRangeMoving((0,6,18),0,-90, 90))
    #time.sleep(2)
    #print(AK.setPitchRangeMoving((-4.8, 15, 1.5), 0, -90, 0, 2000))
    #AK.drawMoveRange2D(-10, 10, 0.2, 10, 30, 0.2, 2.5, -90, 90, 1)
