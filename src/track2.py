from pixy import *
from ctypes import *
from MultiWii import MultiWii
from Pid2 import Pid
from sys import argv
from os import listdir
from os.path import isfile, join
import csv
import time
import collections
import numpy as np
import sys

# Constants for calculating distance all sizes in mm
FOCAL_LEN = 2.8
F_STOP = 2.0
SENS_DIAG = 6.35
IMG_HEIGHT = 200.0                  # frame width pixels
IMG_WIDTH = 320.0                   # frame width pixels
SENS_RATIO = IMG_WIDTH / IMG_HEIGHT
TARG_HEIGHT = 355.6
SENS_HEIGHT = pow(pow(SENS_DIAG, 2)  / (pow(SENS_RATIO, 2) + 1), .5)
Y_FOV = 47                          # Vertical field of view %
DEG_PPX_Y = IMG_HEIGHT / Y_FOV      # % per pixel

def initPID(p, i, d, target, offset_limit):
    pid = Pid(p, i, d)
    pid.set_limit(offset_limit)
    pid.set_reference(target)
    return pid

def sendCommandsToPi(commands, board):
    try:
        board.sendCMD(8, MultiWii.SET_RAW_RC, command)
    except:
        print 'Command not sent'

def logAndPrintCommands(data, csvwriter):
    try:
        print 'time=%.2f x=%3d y=%3d dist=%4d roll=%4d pitch=%4d thrust=%4d yaw=%4d' % tuple([0.0 if x is None else x for x in data])
        csvwriter.writerow(data)
    except TypeError:
        print 'Bad digit in print'

def parseBlock(block):
    # RETURNS :
    # [x, y, inv_size]
    #   - x, y : x & y coordnates for center of detected object
    #   - dist : the inverted size of the detected region. NOTE: This
    #       value is only assigned under the condition that the object is
    #       entirely within the frame or that it is detected as being too
    #       close to the pixy
    min_dist = 1300
    margin = 4 # Number of pixels from edge that will signal out of frame

    # Data range for pixy adjusted for the margin declaring out of frame
    xmin = 1 + margin
    ymin = 1 + margin
    xmax = 319 - margin
    ymax = 198 - margin

    # edge boundaries of block
    l_x = block.x - (block.width / 2);    #left
    r_x = block.x + (block.width / 2);    #right
    t_y = block.y - (block.height / 2);   #top
    b_y = block.y + (block.height / 2);   #bottom

    dist = (F_STOP * TARG_HEIGHT * IMG_HEIGHT) / (block.height * SENS_HEIGHT)

    not_too_close = dist > min_dist
    out_of_x = (l_x <= xmin) or (r_x >= xmax)
    out_of_y = (t_y <= ymin) or (b_y >= ymax)

    if not_too_close and (out_of_x or out_of_y):
        print 'PARTIALLY OUT OF FRAME'
        dist = None
    return [block.x, block.y, dist]

pixy_init()                             # connect camera
blocks = BlockArray(1)                  # array for camera output
board = MultiWii('/dev/ttyUSB0')        # connect arduino

# set up data logging
PATH = '/home/pi/anti-drone-system/data'
filenames = [x for x in listdir(PATH) if isfile(join(PATH, x))]
if len(filenames) != 0:
        datanum = max([int(x[4:-4]) for x in filenames]) + 1
else:
        datanum = 0
filename = 'data' + str(datanum) + '.csv'
datafile = open(join(PATH, filename), 'wb')
csvwriter = csv.writer(datafile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
csvwriter.writerow(['time', 'x', 'y', 'size_inv', 'roll', 'pitch', 'thrust', 'yaw'])
print 'filename=' + filename

# Constant control values
ROLL_OFFSET = 1500              # center roll control value
PITCH_OFFSET = 1507             # center pitch value
THRUST_OFFSET = 1300            # center thrust control value (~hover)
YAW_OFFSET = 1500		        # center yaw control value
PIXEL_X_OFFSET = 160            # center of screen on x-axis
PIXEL_Y_OFFSET = 100            # center of screen on y-axis
LANDING_THRUST = THRUST_OFFSET - 24

# Thrust controller values
T_KP = 1.2
T_KI = 0.04
T_KD = 1.4
thrust_pid = initPID(T_KP, T_KI, T_KD, PIXEL_Y_OFFSET, 50)

# Yaw controller values
Y_KI = 0.0
Y_KD = 0.0
Y_KP = 1.0
yaw_pid = initPID(Y_KP, Y_KI, Y_KD, PIXEL_X_OFFSET, 20)

# Roll controller values
R_KP = 2.0
R_KI = 1.0
R_KD = 0.0
roll_pid = initPID(R_KP, R_KI, R_KD, PIXEL_X_OFFSET, 20)

# Pitch controller values
P_BUFF_LEN = 10      # Size of circular buffer for size_inv values
P_KP = 0.0045
P_KI = 0    #.0005
P_KD = 0.04

while pixy_get_blocks(1, blocks) == 0:
    # use the initial distance as locked distance for flight
    print 'Attempting to lock distance'
[x, y, DIST_OFFSET] = parseBlock(blocks[0])
pitch_pid = initPID(P_KP, P_KI, P_KD, DIST_OFFSET, 40)

pitch_buff = np.full(P_BUFF_LEN, dist) # buffer to act as filter for pitch values
#cum_pitch = sum(pitch_buff)

# Print preliminary flight information
if len(argv) > 1 and argv[1] == 'ARM':
    board.arm()
    print 'Flight controller is ARMED.'
else:
    print 'Running script in SAFE MODE.'

program_start = time.time()
dt = 0.02                       # 50 Hz refresh rate

while True:
    try:
        loop_start = time.time()
        count = pixy_get_blocks(1, blocks)
        # calculate the y offset given current pitch
        board.getData(MultiWii.ATTITUDE)
        y_off = board.attitude['angy'] / DEG_PPX_Y
        if count > 0:
            # detection successful. Calculate axis vlues to be sent to FC
            [x, y, dist] = parseBlock(blocks[0])
            y = y - y_off
            roll = -roll_pid.get_output(x) + ROLL_OFFSET
            thrust = thrust_pid.get_output(y) + THRUST_OFFSET
            yaw = -yaw_pid.get_output(x) + YAW_OFFSET
            if dist is not None:
                #cum_pitch = cum_pitch + (dist - pitch_buff[p_i])
                pitch_buff[p_i] = dist
                dist = min(pitch_buff)  # due to noise, pick min value
                #dist = cum_pitch / P_BUFF_LEN
                pitch = -pitch_pid.get_output(dist) + PITCH_OFFSET
                p_i = (p_i + 1) % P_BUFF_LEN
            else:
                # too close to target to detect distance
                pitch = PITCH_OFFSET - 15
        else:
            # detection failed
            dist = None
            x = None
            y = None
            size_inv = None
            roll = -roll_pid.get_output(PIXEL_X_OFFSET) + ROLL_OFFSET
            pitch = -pitch_pid.get_output(DIST_OFFSET) + PITCH_OFFSET
            thrust = thrust_pid.get_output(PIXEL_Y_OFFSET) + THRUST_OFFSET
            yaw = -yaw_pid.get_output(PIXEL_X_OFFSET) + YAW_OFFSET

        data = [time.time() - program_start, x, y, dist, roll, pitch, thrust, yaw]
        logAndPrintCommands(data, csvwriter)

        command = [roll, pitch, thrust, yaw]
        sendCommandsToPi(command, board)

        time.sleep(dt - (loop_start - time.time()))

    except KeyboardInterrupt:
        print 'Landing mode. Press CTRL+C to stop.'
        while True:
            loop_start = time.time()
            try:
                board.sendCMD(16, MultiWii.SET_RAW_RC, [ROLL_OFFSET,
                                                        PITCH_OFFSET,
                                                        LANDING_THRUST,
                                                        YAW_OFFSET,
                                                        1500,
                                                        1500,
                                                        1500,
                                                        1500])
                time.sleep(dt - (loop_start - time.time()))
            except KeyboardInterrupt:
                datafile.close()
                board.disarm()
                pixy_close()
                break
        break
