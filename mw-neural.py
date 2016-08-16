#!/usr/bin/env python
""" Drone Pilot - Control of MRUAV """
""" mw-neural.py: Totally experimental stuff. """

__author__ = "Aldo Vargas"
__copyright__ = "Copyright 2015 Altax.net"

__license__ = "GPL"
__version__ = "1.2"
__maintainer__ = "Aldo Vargas"
__email__ = "alduxvm@gmail.com"
__status__ = "Development"

import time, datetime, csv, threading
from math import *
from modules.utils import *
from modules.pyMultiwii import MultiWii
import modules.UDPserver as udp

import pandas as pd
import numpy as np
import modules.pyrenn as prn

# Main configuration
logging = True
update_rate = 0.01 # 100 hz loop cycle
vehicle_weight = 0.84 # Kg
u0 = 1000 # Zero throttle command
uh = 1360 # Hover throttle command
kt = vehicle_weight * g / (uh-u0)
ky = 500 / pi # Yaw controller gain

# MRUAV initialization
vehicle = MultiWii("/dev/ttyUSB0")
vehicle.getData(MultiWii.ATTITUDE)

# Position coordinates [x, y, x] 
desiredPos = {'x':0.0, 'y':0.0, 'z':1.0} # Set at the beginning (for now...)
currentPos = {'x':0.0, 'y':0.0, 'z':0.0} # It will be updated using UDP

# Initialize RC commands and pitch/roll to be sent to the MultiWii 
rcCMD = [1500,1500,1500,1000]
desiredRoll = 1500
desiredPitch = 1500
desiredThrottle = 1000
desiredYaw = 1500

# Controller PID's gains (Gains are considered the same for pitch and roll)
p_gains = {'kp': 2.61, 'ki':0.57, 'kd':3.41, 'iMax':2, 'filter_bandwidth':50} # Position Controller gains
h_gains = {'kp': 4.64, 'ki':1.37, 'kd':4.55, 'iMax':2, 'filter_bandwidth':50} # Height Controller gains
y_gains = {'kp': 1.0,  'ki':0.0,  'kd':0.0,  'iMax':2, 'filter_bandwidth':50} # Yaw Controller gains

# PID modules initialization
rollPID =   PID(p_gains['kp'], p_gains['ki'], p_gains['kd'], p_gains['filter_bandwidth'], 0, 0, update_rate, p_gains['iMax'], -p_gains['iMax'])
rPIDvalue = 0.0
pitchPID =  PID(p_gains['kp'], p_gains['ki'], p_gains['kd'], p_gains['filter_bandwidth'], 0, 0, update_rate, p_gains['iMax'], -p_gains['iMax'])
pPIDvalue = 0.0
heightPID = PID(h_gains['kp'], h_gains['ki'], h_gains['kd'], h_gains['filter_bandwidth'], 0, 0, update_rate, h_gains['iMax'], -h_gains['iMax'])
hPIDvalue = 0.0
yawPID =    PID(y_gains['kp'], y_gains['ki'], y_gains['kd'], y_gains['filter_bandwidth'], 0, 0, update_rate, y_gains['iMax'], -y_gains['iMax'])
yPIDvalue = 0.0

# Filters initialization
f_yaw   = low_pass(20,update_rate)
f_pitch = low_pass(20,update_rate)
f_roll  = low_pass(20,update_rate)

# Function to update commands and attitude to be called by a thread
def control():
    global vehicle, rcCMD
    global rollPID, pitchPID, heightPID, yawPID
    global desiredPos, currentPos
    global desiredRoll, desiredPitch, desiredThrottle
    global rPIDvalue, pPIDvalue, yPIDvalue
    global f_yaw, f_pitch, f_roll
    global ky

    # Start Neural network
    net = prn.loadNN('modules/network.csv')
    # Array of inputs to network 
    inputs = np.zeros((10,1))

    while True:
        if udp.active:
            print "UDP server is active..."
            break
        else:
            print "Waiting for UDP server to be active..."
        time.sleep(0.5)

    try:
        if logging:
            st = datetime.datetime.fromtimestamp(time.time()).strftime('%m_%d_%H-%M-%S')+".csv"
            f = open("logs/mw-"+st, "w")
            logger = csv.writer(f)
            # V -> vehicle | P -> pilot (joystick) | D -> desired position | M -> motion capture | C -> commanded controls | NN -> Neural Network prediction
            logger.writerow(('timestamp','Vroll','Vpitch','Vyaw','Proll','Ppitch','Pyaw','Pthrottle', \
                             'x','y','z','Dx','Dy','Dz','Mroll','Mpitch','Myaw','Mode','Croll','Cpitch','Cyaw','Cthrottle', \
                             'NNroll','NNpitch','NNyaw','NNx','NNy','NNz'))
        while True:
            # Variable to time the loop
            current = time.time()
            elapsed = 0

            # Update joystick commands from UDP communication, order (roll, pitch, yaw, throttle)
            rcCMD[0] = udp.message[0]
            rcCMD[1] = udp.message[1]
            rcCMD[2] = udp.message[2]
            rcCMD[3] = udp.message[3]

            # Coordinate map from Optitrack in the MAST Lab: X, Y, Z. NED: If going up, Z is negative. 
            ######### WALL ########
            #Door      | x+       |
            #          |          |
            #          |       y+ |
            #---------------------|
            # y-       |          |
            #          |          |
            #        x-|          |
            #######################
            # Update current position of the vehicle
            currentPos['x'] = udp.message[5]
            currentPos['y'] = udp.message[6]
            currentPos['z'] = -udp.message[7]

            # Update Attitude 
            vehicle.getData(MultiWii.ATTITUDE)

            # Filter new values before using them
            heading = f_yaw.update(udp.message[12])

            # PID updating, Roll is for Y and Pitch for X, Z is negative
            rPIDvalue = rollPID.update(desiredPos['y'] - currentPos['y'])
            pPIDvalue = pitchPID.update(desiredPos['x'] - currentPos['x'])
            hPIDvalue = heightPID.update(desiredPos['z'] - currentPos['z'])
            yPIDvalue = yawPID.update(0.0 - heading)
            
            # Heading must be in radians, MultiWii heading comes in degrees, optitrack in radians
            sinYaw = sin(heading)
            cosYaw = cos(heading)

            # Conversion from desired accelerations to desired angle commands
            desiredRoll  = toPWM(degrees( (rPIDvalue * cosYaw + pPIDvalue * sinYaw) * (1 / g) ),1)
            desiredPitch = toPWM(degrees( (pPIDvalue * cosYaw - rPIDvalue * sinYaw) * (1 / g) ),1)
            desiredThrottle = ((hPIDvalue + g) * vehicle_weight) / (cos(f_pitch.update(radians(vehicle.attitude['angx'])))*cos(f_roll.update(radians(vehicle.attitude['angy']))))
            desiredThrottle = (desiredThrottle / kt) + u0
            desiredYaw = 1500 - (yPIDvalue * ky)

            # Limit commands for safety
            if udp.message[4] == 1:
                rcCMD[0] = limit(desiredRoll,1000,2000)
                rcCMD[1] = limit(desiredPitch,1000,2000)
                rcCMD[2] = limit(desiredYaw,1000,2000)
                rcCMD[3] = limit(desiredThrottle,1000,2000)
                mode = 'Auto'
            else:
                # Prevent integrators/derivators to increase if they are not in use
                rollPID.resetIntegrator()
                pitchPID.resetIntegrator()
                heightPID.resetIntegrator()
                yawPID.resetIntegrator()
                mode = 'Manual'
            rcCMD = [limit(n,1000,2000) for n in rcCMD]

            # Neural network update
            # Order of the inputs -> roll, pitch, yaw, throttle, vehicle roll, vehicle pitch, motion capture yaw, x, y, z
            np.put(inputs, [0,1,2,3,4,5,6,7,8,9], \
                [rcCMD[0], rcCMD[1], rcCMD[2], rcCMD[3], \
                 vehicle.attitude['angx'], vehicle.attitude['angy'], udp.message[9], \
                 currentPos['x'], currentPos['y'], currentPos['z'] ])
            # Get output of neural network
            outputs = prn.NNOut(inputs,net)

            # Send commands to vehicle
            vehicle.sendCMD(8,MultiWii.SET_RAW_RC,rcCMD)

            row =   (time.time(), \
                    vehicle.attitude['angx'], vehicle.attitude['angy'], vehicle.attitude['heading'], \
                    #vehicle.rawIMU['ax'], vehicle.rawIMU['ay'], vehicle.rawIMU['az'], vehicle.rawIMU['gx'], vehicle.rawIMU['gy'], vehicle.rawIMU['gz'], \
                    #vehicle.rcChannels['roll'], vehicle.rcChannels['pitch'], vehicle.rcChannels['throttle'], vehicle.rcChannels['yaw'], \
                    udp.message[0], udp.message[1], udp.message[2], udp.message[3], \
                    currentPos['x'], currentPos['y'], currentPos['z'], desiredPos['x'], desiredPos['y'], desiredPos['z'], \
                    udp.message[11], udp.message[12], udp.message[13], \
                    udp.message[7], \
                    rcCMD[0], rcCMD[1], rcCMD[2], rcCMD[3], \
                    outputs[0,0], outputs[1,0], outputs[2,0], outputs[3,0], outputs[4,0], outputs[5,0] )
            if logging:
                logger.writerow(row)

            print "Mode: %s | Z: %0.3f | X: %0.3f | Y: %0.3f " % (mode, currentPos['z'], currentPos['x'], currentPos['y'])
            #print "Mode: %s | heading: %0.3f | desiredYaw: %0.3f" % (mode, heading, desiredYaw)

            # Wait until the update_rate is completed 
            while elapsed < update_rate:
                elapsed = time.time() - current

    except Exception,error:
        print "Error in control thread: "+str(error)

if __name__ == "__main__":
    try:
        logThread = threading.Thread(target=control)
        logThread.daemon=True
        logThread.start()
        udp.startTwisted()
    except Exception,error:
        print "Error on main: "+str(error)
        vehicle.ser.close()
    except KeyboardInterrupt:
        print "Keyboard Interrupt, exiting."
        exit()