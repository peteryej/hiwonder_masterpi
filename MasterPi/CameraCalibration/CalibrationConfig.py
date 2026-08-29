#相邻两个角点间的实际距离，单位cm(actual distance between two adjacent corners, in unit of cm)
corners_length = 2.1

#木块边长3cm(The length of each side of the block is 3 cm.)
square_length = 3

#标定棋盘大小, 列， 行, 指内角点个数，非棋盘格(The calibration chessboard size is determined by the number of internal corners, excluding the non-chessboard corners, in both the row and column directions.)
calibration_size = (7, 7)

#采集标定图像存储路径(the storage path for the collected calibration images)
save_path = '/home/pi/MasterPi/CameraCalibration/calibration_images/'

#标定参数存储路径(the storage path for the calibration parameters)
calibration_param_path = '/home/pi/MasterPi/CameraCalibration/calibration_param'

#映射参数存储路径(the storage path for the mapping parameters)
map_param_path = '/home/pi/MasterPi/CameraCalibration/map_param'
