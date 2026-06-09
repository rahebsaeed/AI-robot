import serial
import time

class Robot:
    def __init__(self, port="/dev/myserial", baudrate=115200):
        try:
            # Using logical name from robot_init.sh
            self.ser = serial.Serial(port, baudrate, timeout=1)
            print(f"Connected to Rosmaster X3 PLUS on {port}")
        except Exception as e:
            print(f"Could not connect to robot on {port}: {e}")
            # Fallback to standard ttyUSB1 if logic link fails
            try:
                self.ser = serial.Serial("/dev/ttyUSB1", baudrate, timeout=1)
                print(f"Connected on fallback /dev/ttyUSB1")
            except:
                self.ser = None

    def move(self, direction, speed):
        """
        Standard Yaboom Protocol: $4WD,vx,vy,vw#
        Values normally scaled -100 to 100
        """
        vx, vy, vw = 0, 0, 0
        s = int(speed * 100)
        
        if direction == "forward": vx = s
        elif direction == "backward": vx = -s
        elif direction == "left": vw = -s
        elif direction == "right": vw = s
        elif direction == "stop": vx, vy, vw = 0, 0, 0

        cmd = f"$4WD,{vx},{vy},{vw}#\n"
        print(f"Robot Command: {cmd.strip()}")
        if self.ser:
            try:
                self.ser.write(cmd.encode())
            except Exception as e:
                print(f"Serial Error: {e}")

    def control_arm(self, action):
        """
        Simplified arm actions for Rosmaster
        """
        print(f"Robot Arm: {action}")
        # Note: Arm commands usually involve specific joint indices ($ARM,j1,j2,j3,j4,j5,j6#)
        # For now, we print and handle if the user provides specific joint logic later
        if self.ser:
            if action == "searching":
                # Example preset for searching
                self.ser.write(b"$ARM,90,90,90,90,0,0#\n")
            elif action == "home":
                self.ser.write(b"$ARM,90,10,10,10,90,0#\n")
