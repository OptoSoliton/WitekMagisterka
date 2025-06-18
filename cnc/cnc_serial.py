import serial
import serial.tools.list_ports
import time

class CNCSerial:
    def __init__(self):
        self.serial_port = None
        self.connected = False

    def connect_cnc(self, port):
        try:
            self.serial_port = serial.Serial(
                port, baudrate=115200, timeout=1)
            self.connected = True
            return "Connected to {}".format(port)
        except Exception as e:
            return "Error: {}".format(e)

    def disconnect_cnc(self):
        if self.serial_port:
            self.serial_port.close()
            self.connected = False
            return "Disconnected from CNC"
        return "Not connected to CNC"

    def send_gcode(self, command):
        if self.serial_port and self.connected:
            self.serial_port.write((command + '\n').encode())
            return f"Command sent: {command}"
        return "Not connected to CNC"

    def wait_for_ending_move(self):
        if self.serial_port and self.connected:
            self.serial_port.write(('?\n').encode())
            time.sleep(0.1)
            response = self.serial_port.read_until().decode().strip()
            if "Idle" in response:
                return True
        return False

    def list_serial_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]
