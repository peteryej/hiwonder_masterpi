# !/usr/bin/env python3
# encoding: utf-8
import os
import sys
import time
import threading
import sqlite3 as sql
import common.yaml_handle as yaml_handle


class ActionGroupController:
    runningAction = False
    stopRunning = False
    
    def __init__(self, board = None):
        self.board = board
        self.runningAction = False
        self.stopRunning = False
        
    def stop_action_group(self):
        self.stopRunning = True
    
    def runAction(self, actNum):
        '''
            运行动作组，无法发送stop停止信号
            :param actNum: 动作组名字 ， 字符串类型
            :return:
        '''
        if actNum is None:
            return
            
        actNum = "/home/pi/MasterPi/action_groups/" + actNum + ".d6a"
        self.stopRunning = False
        if os.path.exists(actNum):
            ag = sql.connect(actNum)
            cu = ag.cursor()
            cu.execute("select * from ActionGroup")
            deviation_data = yaml_handle.get_yaml_data(yaml_handle.Deviation_file_path)
            while True:
                act = cu.fetchone()
                if self.stopRunning:
                    self.stopRunning = False
                    break
                if act is not None:
                    data = [[1,act[2] + deviation_data['1']],
                            [3,act[3] + deviation_data['3']],
                            [4,act[4] + deviation_data['4']],
                            [5,act[5] + deviation_data['5']],
                            [6,act[6] + deviation_data['6']]]
                    if self.board:
                        self.board.pwm_servo_set_position(float(act[1])/1000.0,data)
                    
                    if self.stopRunning:
                        self.stopRunning = False
                        break
                    time.sleep(float(act[1])/1000.0)
                else:
                    break
            self.runningAction = False
            cu.close()
            ag.close()
        else:
            self.runningAction = False
            print("未能找到动作组文件")
        
