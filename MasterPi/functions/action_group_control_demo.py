#!/usr/bin/python3
# coding=utf8
    
print('''
**********************************************************
********************功能:动作组调用例程************************
**********************************************************
----------------------------------------------------------
Official website:https://www.hiwonder.com
Online mall:https://hiwonder.tmall.com
----------------------------------------------------------
Tips:
 * 按下Ctrl+C可关闭此次程序运行，若失败请多次尝试！
----------------------------------------------------------
''')

if __name__ == '__main__':
    from common.ros_robot_controller_sdk import Board
    from common.action_group_control import ActionGroupController
    
    board = Board()
    AGC = ActionGroupController(board)
    
    AGC.runAction('stand')