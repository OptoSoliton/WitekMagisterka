import tkinter as tk
from gui.my_gui import MyGUI
from cnc.cnc_serial import CNCSerial
from nir1.wasatch import Wasatch
import sys
import logging

from crash_guard import configure_crash_logging, report_tk_exception

if __name__ == "__main__":
    crash_log = configure_crash_logging()
    root = tk.Tk()
    root.report_callback_exception = report_tk_exception
    serial_connection = CNCSerial()
    wasatch = Wasatch(root, sys.argv)
    gui = MyGUI(root, serial_connection, wasatch)
    logging.info("Application started. Crash and runtime logs: %s", crash_log)
    root.mainloop()
