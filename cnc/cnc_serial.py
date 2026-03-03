import serial
import serial.tools.list_ports
import time
import re
import threading
import logging


log = logging.getLogger(__name__)


class CNCSerial:
    def __init__(self):
        self.serial_port = None
        self.connected = False
        self._io_lock = threading.Lock()

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
            try:
                with self._io_lock:
                    self.serial_port.write((command + '\n').encode())
                return f"Command sent: {command}"
            except Exception as e:
                self.connected = False
                return f"Serial write error: {e}"
        return "Not connected to CNC"

    def wait_for_ending_move(self):
        if self.serial_port and self.connected:
            try:
                with self._io_lock:
                    self.serial_port.write(('?\n').encode())
                    time.sleep(0.1)
                    response = self.serial_port.read_until().decode(errors='replace').strip()
                if "Idle" in response:
                    return True
            except Exception:
                self.connected = False
        return False

    def list_serial_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]

    def get_current_position(self):
        if self.serial_port and self.connected:
            try:
                with self._io_lock:
                    self.serial_port.reset_input_buffer()
                    self.serial_port.write(b'?\n')
                    time.sleep(0.05)
                    response = self.serial_port.read_until().decode(errors='replace').strip()
                match = re.search(r'WPos:([\-\d\.]+),([\-\d\.]+),([\-\d\.]+)', response)
                if match:
                    return {
                        'X': float(match.group(1)),
                        'Y': float(match.group(2)),
                        'Z': float(match.group(3)),
                    }
            except Exception as exc:
                self.connected = False
                log.warning("Failed to query CNC position: %s", exc)
        return None

    def query_position(self):
        """Backward-compatible wrapper for current position queries."""
        return self.get_current_position()

