import tkinter as tk
from gui.my_gui import MyGUI
from cnc.cnc_serial import CNCSerial
from nir1.wasatch import Wasatch
import sys

if __name__ == "__main__":
    root = tk.Tk()
    serial_connection = CNCSerial()
    wasatch = Wasatch(root, sys.argv)
    gui = MyGUI(root, serial_connection, wasatch)
    root.mainloop()