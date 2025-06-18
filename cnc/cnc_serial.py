import serial
import serial.tools.list_ports
import time

class CNCSerial:
    def __init__(self):
        self.serial_port = None
        self.connected = False
        self._status_thread = None   # ◄─ nowy: monitoring “?”

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

    def wait_for_ending_move(self, timeout=10):
        """Czeka aż GRBL zwróci Idle lub minie *timeout* (s)."""
        if not (self.serial_port and self.connected):
            return False

        start = time.time()
        while time.time() - start < timeout:
            self.serial_port.write(b'?\n')
            time.sleep(0.05)
            response = self.serial_port.read_until().decode(errors='ignore').strip()
            if 'Idle' in response:
                return True
        return False
    
    def start_status_monitor(self, callback=None, interval=0.5):
        """Co *interval* sekund wysyła '?' i przekazuje linię do *callback*."""
        if not (self.serial_port and self.connected):
            return
        def _worker():
            while self.connected:
                try:
                    self.serial_port.write(b'?\n')
                    line = self.serial_port.read_until().decode(errors='ignore').strip()
                    if callback and line:
                        callback(line)
                except Exception:
                    pass
                time.sleep(interval)
        import threading; threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def list_serial_ports(self):
        return [port.device for port in serial.tools.list_ports.comports()]

    