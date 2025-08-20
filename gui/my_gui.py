import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import queue
import time
import threading
import numpy as np
import logging
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure  # required for embedded plots

# Global padding to keep layout compact
PADX = 3
PADY = 2
FRAME_KWARGS = dict(padding=(PADX, PADY))


class TkLogHandler(logging.Handler):
    """Forward logging records to the GUI log window."""

    def __init__(self, gui):
        super().__init__()
        self.gui = gui

    def emit(self, record):
        try:
            msg = self.format(record)
            self.gui.log(msg)
        except Exception:
            pass


class ToolTip:
    """Simple tooltip that appears on hover."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        label = tk.Label(tw, text=self.text, background="#ffffe0", relief="solid", borderwidth=1)
        label.pack(ipadx=1)
        tw.wm_geometry(f"+{x}+{y}")

    def hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None

class MyGUI:
    def __init__(self, root, serial_connection, wasatch):
        self.root = root
        self.serial = serial_connection
        self.wasatch = wasatch
        self.root.title("CNC & Wasatch Controller")
        # default to Full HD layout to keep controls and plots visible
        self.root.geometry("1920x1080")
        try:
            self.root.state('zoomed')
        except Exception:
            pass

        # scrollable container so every widget is accessible even on small screens
        self.canvas = tk.Canvas(self.root)
        self.vscroll = ttk.Scrollbar(self.root, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vscroll.pack(side="right", fill="y")

        self.container = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.container, anchor='nw')
        self.container.bind(
            '<Configure>',
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        )

        # Actual XYZ position
        self.current_position = {'X': 0, 'Y': 0, 'Z': 0}
        # Positions 1-5 used to define scan volume
        # 1 - origin, 2 - X extent, 4 - Y extent, 5 - Z extent, 3 optional
        self.user_positions = {
            '1': None,
            '2': None,
            '3': None,
            '4': None,
            '5': None,
        }
        self.dark_taken = False

        self.integration_time = 10
        self.scans_to_average = 1
        self.boxcar_half_width = 0
        self.delay_ms = 1000
        self.max_spectra = 0

        self.samples_count_x = 1
        self.samples_count_y = 1
        self.samples_count_z = 1

        self.output_file = None
        self.map_zoomed = False

        self.setup_ui()
        # forward Wasatch logger messages to GUI log
        handler = TkLogHandler(self)
        self.wasatch.set_logger_handler(handler)
        self.running = False
        self.paused = False
        self.measure_thread = threading.Thread()
        self.pause_position = None

    def setup_ui(self):

        # Left and right part of window inside scrollable container
        self.left_frame = ttk.Frame(self.container)
        self.left_frame.grid(row=0, column=0, sticky='nw')

        self.right_frame = ttk.Frame(self.container)
        self.right_frame.grid(row=0, column=1, sticky='nw')

        # split right side into controls and plots to avoid oversized grid rows
        self.controls_frame = ttk.Frame(self.right_frame)
        self.controls_frame.grid(row=0, column=0, sticky='nw')
        self.plots_frame = ttk.Frame(self.right_frame)
        self.plots_frame.grid(row=0, column=1, sticky='ne')

        # Serial connection frame
        self.serial_connection_frame = ttk.LabelFrame(self.left_frame, text="CNC serial connection", **FRAME_KWARGS)
        self.serial_connection_frame.grid(row=0, column=0, padx=PADX, pady=PADY, sticky="nw")

        self.serial_port_label = ttk.Label(self.serial_connection_frame, text="Port COM:")
        self.serial_port_label.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.serial_ports = self.serial.list_serial_ports()
        self.serial_port_combobox = ttk.Combobox(self.serial_connection_frame, values=self.serial_ports)
        self.serial_port_combobox.grid(row=0, column=1, padx=PADX, pady=PADY)

        self.connect_button = ttk.Button(self.serial_connection_frame, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # Control frame for movement
        self.control_frame = ttk.LabelFrame(self.left_frame, text="CNC control", **FRAME_KWARGS)
        self.control_frame.grid(row=1, column=0, padx=PADX, pady=PADY, sticky="nw")

        # Movement buttons
        button = ttk.Button(self.control_frame, text='\u2190', 
        command=lambda d='\u2190': self.move(d)) # Left
        button.grid(row=1, column=0, padx=PADX, pady=PADY)
    
        button = ttk.Button(self.control_frame, text='\u2191', command=lambda d='\u2191': self.move(d)) # Up
        button.grid(row=0, column=1, padx=PADX, pady=PADY)
    
        button = ttk.Button(self.control_frame, text='\u2192', command=lambda d='\u2192': self.move(d)) # Right
        button.grid(row=1, column=2, padx=PADX, pady=PADY)
    
        button = ttk.Button(self.control_frame, text='\u2193', command=lambda d='\u2193': self.move(d)) # Down
        button.grid(row=2, column=1,  padx=PADX, pady=PADY)

        ttk.Label(
            self.control_frame,
            text="←/→ = X axis, ↑/↓ = Y axis"
        ).grid(row=3, column=0, columnspan=3, pady=(0, PADY))

        # Z-axis control
        self.up_button = ttk.Button(self.control_frame, text="Up", command=lambda: self.move('Up'))
        self.up_button.grid(row=0, column=3, padx=PADX, pady=PADY)

        self.down_button = ttk.Button(self.control_frame, text="Down", command=lambda: self.move('Down'))
        self.down_button.grid(row=2, column=3, padx=PADX, pady=PADY)

        # Adding "0,0" Button to reset position
        self.zero_button = ttk.Button(self.control_frame, text="Set 0.0", command=lambda: self.set_position_zero())
        self.zero_button.grid(row=1, column=1, padx=PADX, pady=PADY)

        # Step and Speed frame
        self.step_speed_frame = ttk.LabelFrame(self.left_frame, text="CNC control parameters", **FRAME_KWARGS)
        self.step_speed_frame.grid(row=2, column=0, padx=PADX, pady=PADY, sticky="nw")

        self.step_label = ttk.Label(self.step_speed_frame, text="Step:")
        self.step_label.grid(row=0, column=0, padx=PADX, pady=PADY)
        self.step_entry = ttk.Entry(self.step_speed_frame)
        self.step_entry.grid(row=0, column=1, padx=PADX, pady=PADY)
        self.step_entry.insert(tk.END, '10')  # Default value

        self.speed_label = ttk.Label(self.step_speed_frame, text="Speed:")
        self.speed_label.grid(row=0, column=2, padx=PADX, pady=PADY)
        self.speed_entry = ttk.Entry(self.step_speed_frame)
        self.speed_entry.grid(row=0, column=3, padx=PADX, pady=PADY)
        self.speed_entry.insert(tk.END, '1000')  # Default value

        # Position setting and test movement frame
        self.position_frame = ttk.LabelFrame(self.left_frame, text="Position configuration", **FRAME_KWARGS)
        self.position_frame.grid(row=3, column=0, padx=PADX, pady=PADY, sticky="nw")

        # Position buttons arranged as 1 3 / 2 4 with 5 on the right
        self.set_button_1 = ttk.Button(self.position_frame, text="Set 1", command=lambda: self.set_position(1))
        self.set_button_1.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.set_button_3 = ttk.Button(self.position_frame, text="Set 3", command=lambda: self.set_position(3))
        self.set_button_3.grid(row=0, column=1, padx=PADX, pady=PADY)

        self.set_button_2 = ttk.Button(self.position_frame, text="Set 2", command=lambda: self.set_position(2))
        self.set_button_2.grid(row=1, column=0, padx=PADX, pady=PADY)

        self.set_button_4 = ttk.Button(self.position_frame, text="Set 4", command=lambda: self.set_position(4))
        self.set_button_4.grid(row=1, column=1, padx=PADX, pady=PADY)

        self.set_button_5 = ttk.Button(
            self.position_frame,
            text="Set 5 (set high point)",
            command=lambda: self.set_position(5),
        )
        self.set_button_5.grid(row=0, column=2, rowspan=2, padx=(PADX*3, PADX), pady=PADY)

        # goto buttons aligned with the set buttons
        self.goto_button_1 = ttk.Button(self.position_frame, text="Go to 1", command=lambda: self.goto_position(1))
        self.goto_button_1.grid(row=0, column=3, padx=PADX, pady=PADY)
        self.goto_button_3 = ttk.Button(self.position_frame, text="Go to 3", command=lambda: self.goto_position(3))
        self.goto_button_3.grid(row=0, column=4, padx=PADX, pady=PADY)
        self.goto_button_2 = ttk.Button(self.position_frame, text="Go to 2", command=lambda: self.goto_position(2))
        self.goto_button_2.grid(row=1, column=3, padx=PADX, pady=PADY)
        self.goto_button_4 = ttk.Button(self.position_frame, text="Go to 4", command=lambda: self.goto_position(4))
        self.goto_button_4.grid(row=1, column=4, padx=PADX, pady=PADY)
        self.goto_button_5 = ttk.Button(self.position_frame, text="Go to 5", command=lambda: self.goto_position(5))
        self.goto_button_5.grid(row=0, column=5, rowspan=2, padx=PADX, pady=PADY)

        self.test_button = ttk.Button(self.position_frame, text="Test", command=self.test_positions)
        self.test_button.grid(row=2, column=3, padx=PADX, pady=PADY)

        # Adding "Init" Button to send initialization commands
        self.init_button = ttk.Button(self.position_frame, text="Goto 0.0", command=self.move_to_zero)
        self.init_button.grid(row=2, column=4,  padx=PADX, pady=PADY)
        
        # Manual command frame
        self.manual_command_frame = ttk.LabelFrame(self.left_frame, text="Manual command", **FRAME_KWARGS)
        self.manual_command_frame.grid(row=4, column=0, padx=PADX, pady=PADY, sticky="nw")

        self.manual_command_label = ttk.Label(self.manual_command_frame, text="Command:")
        self.manual_command_label.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.manual_command_entry = ttk.Entry(self.manual_command_frame, width=30)
        self.manual_command_entry.grid(row=0, column=1,  padx=PADX, pady=PADY)

        self.manual_command_button = ttk.Button(self.manual_command_frame, text="Send", command=self.send_manual_command)
        self.manual_command_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # Log frame
        self.log_frame = ttk.LabelFrame(self.left_frame, text="Log", **FRAME_KWARGS)
        self.log_frame.grid(row=5, column=0, padx=PADX, pady=PADY, sticky="nw")

        self.log_text = scrolledtext.ScrolledText(self.log_frame, wrap=tk.WORD, height=8, width=50)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Spectrometer connection frame
        self.wasatch_connection_frame = ttk.LabelFrame(self.controls_frame, text="Wasatch connection", **FRAME_KWARGS)
        self.wasatch_connection_frame.pack(fill='x', padx=PADX, pady=PADY)

        self.wasatch_connect_button = ttk.Button(self.wasatch_connection_frame, text="Connect spectrometer", command=self.connect_spectrometer)
        self.wasatch_connect_button.grid(row=0, column=0, padx=PADX, pady=PADY)

        # File path selection for saving spectra
        self.file_path_frame = ttk.LabelFrame(self.controls_frame, text="File path for spectra", **FRAME_KWARGS)
        self.file_path_frame.pack(fill='x', padx=PADX, pady=PADY)

        self.file_path_label = ttk.Label(self.file_path_frame, text="File path:")
        self.file_path_label.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.file_path_entry = ttk.Entry(self.file_path_frame, width=30)
        self.file_path_entry.grid(row=0, column=1, padx=PADX, pady=PADY)

        self.file_path_button = ttk.Button(self.file_path_frame, text="Browse", command=self.browse_file_path)
        self.file_path_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # Generate default output path on startup
        self.ensure_file_path()

        # Wasatch parameters
        self.wasatch_parameters_frame = ttk.LabelFrame(self.controls_frame, text="Parameters", **FRAME_KWARGS)
        self.wasatch_parameters_frame.pack(fill='x', padx=PADX, pady=PADY)

        # Integration time control
        self.integration_time_label = ttk.Label(self.wasatch_parameters_frame, text="Integration Time (ms):")
        self.integration_time_label.grid(row=0, column=0, padx=PADX, pady=PADY)
        self.integration_time_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.integration_time_entry.grid(row=0, column=1, padx=PADX, pady=PADY)
        self.integration_time_entry.insert(tk.END, str(self.integration_time))  # Default value

        self.set_integration_time_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_integration_time)
        self.set_integration_time_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # Scans to average control
        self.scans_to_average_label = ttk.Label(self.wasatch_parameters_frame, text="Scans to Average:")
        self.scans_to_average_label.grid(row=1, column=0, padx=PADX, pady=PADY)
        self.scans_to_average_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.scans_to_average_entry.grid(row=1, column=1, padx=PADX, pady=PADY)
        self.scans_to_average_entry.insert(tk.END, str(self.scans_to_average))  # Default value

        self.set_scans_to_average_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_scans_to_average)
        self.set_scans_to_average_button.grid(row=1, column=2, padx=PADX, pady=PADY)

        # Boxcar half width
        self.boxcar_half_width_label = ttk.Label(self.wasatch_parameters_frame, text="Boxcar half width:")
        self.boxcar_half_width_label.grid(row=2, column=0, padx=PADX, pady=PADY)
        self.boxcar_half_width_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.boxcar_half_width_entry.grid(row=2, column=1, padx=PADX, pady=PADY)
        self.boxcar_half_width_entry.insert(tk.END, str(self.boxcar_half_width))
        ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_boxcar_half_width).grid(row=2, column=2, padx=PADX, pady=PADY)

        # Delay ms
        self.delay_ms_label = ttk.Label(self.wasatch_parameters_frame, text="Delay (ms):")
        self.delay_ms_label.grid(row=3, column=0, padx=PADX, pady=PADY)
        self.delay_ms_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.delay_ms_entry.grid(row=3, column=1, padx=PADX, pady=PADY)
        self.delay_ms_entry.insert(tk.END, str(self.delay_ms))
        ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_delay_ms).grid(row=3, column=2, padx=PADX, pady=PADY)

        self.max_spectra_label = ttk.Label(self.wasatch_parameters_frame, text="Max Spectra:")
        self.max_spectra_label.grid(row=4, column=0, padx=PADX, pady=PADY)
        self.max_spectra_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.max_spectra_entry.grid(row=4, column=1, padx=PADX, pady=PADY)
        self.max_spectra_entry.insert(tk.END, str(self.max_spectra))  # Default value

        self.set_max_spectra_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_max_spectra)
        self.set_max_spectra_button.grid(row=4, column=2, padx=PADX, pady=PADY)

        # Measuring frame
        self.wasatch_measure_frame = ttk.LabelFrame(self.controls_frame, text="Measuring", **FRAME_KWARGS)
        self.wasatch_measure_frame.pack(fill='x', padx=PADX, pady=PADY)

        self.wasatch_samples_countX_label = ttk.Label(self.wasatch_measure_frame, text="Sample count X axis")
        self.wasatch_samples_countX_label.grid(row=0, column=1, padx=PADX, pady=PADY)
        self.wasatch_samples_countX_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countX_entry.grid(row=0, column=2, padx=PADX, pady=PADY)
        self.wasatch_samples_countX_entry.insert(tk.END, '10')  # Default value

        self.wasatch_samples_countY_label = ttk.Label(self.wasatch_measure_frame, text="Sample count Y axis")
        self.wasatch_samples_countY_label.grid(row=1, column=1, padx=PADX, pady=PADY)
        self.wasatch_samples_countY_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countY_entry.grid(row=1, column=2, padx=PADX, pady=PADY)
        self.wasatch_samples_countY_entry.insert(tk.END, '10')  # Default value

        self.wasatch_samples_countZ_label = ttk.Label(self.wasatch_measure_frame, text="Sample count Z axis")
        self.wasatch_samples_countZ_label.grid(row=2, column=1, padx=PADX, pady=PADY)
        self.wasatch_samples_countZ_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countZ_entry.grid(row=2, column=2, padx=PADX, pady=PADY)
        self.wasatch_samples_countZ_entry.insert(tk.END, '1')  # Default value

        for entry in (
            self.wasatch_samples_countX_entry,
            self.wasatch_samples_countY_entry,
            self.wasatch_samples_countZ_entry,
        ):
            entry.bind("<KeyRelease>", lambda e: self.calculate_predicted_points())

        self.wasatch_dark_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Dark",
            command=self.run_dark,
            state=tk.DISABLED,
        )
        self.wasatch_dark_button.grid(row=3, column=0, columnspan=2, padx=PADX, pady=PADY)
        ToolTip(self.wasatch_dark_button, "Record dark reference at current position")

        self.wasatch_light_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Light",
            command=self.run_light,
            state=tk.DISABLED,
        )
        self.wasatch_light_button.grid(row=3, column=2, columnspan=2, padx=PADX, pady=PADY)
        ToolTip(self.wasatch_light_button, "Record light reference at current position")

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.wasatch_measure_frame, orient="horizontal", length=300,  mode="determinate")
        self.progress_bar.grid(row=4, column=0, columnspan=4, padx=PADX, pady=PADY)

        # Progress bar label
        self.progress_label = ttk.Label(self.wasatch_measure_frame, text="0 %", anchor="center")
        self.progress_label.grid(row=4, column=3, padx=PADX, pady=PADY)

        self.run_once_label = ttk.Label(self.wasatch_measure_frame, text="Label:")
        self.run_once_label.grid(row=5, column=0, padx=PADX, pady=PADY)

        self.run_once_description = ttk.Entry(self.wasatch_measure_frame, width=30)
        self.run_once_description.grid(row=5, column=1, columnspan=2, padx=PADX, pady=PADY)

        self.run_once_button = ttk.Button(self.wasatch_measure_frame, text="Run once", command=self.start_measurement_once)
        self.run_once_button.grid(row=6, column=0, padx=PADX, pady=PADY)
        ToolTip(self.run_once_button, "Acquire a single measurement at current position")

        self.run_area_button = ttk.Button(self.wasatch_measure_frame, text="Run area", command=self.start_measurement)
        self.run_area_button.grid(row=6, column=1, padx=PADX, pady=PADY)
        ToolTip(self.run_area_button, "Start scan over defined area")

        self.wasatch_stop_button = ttk.Button(self.wasatch_measure_frame, text="Stop", command=self.stop_measurement)
        self.wasatch_stop_button.grid(row=6, column=2, padx=PADX, pady=PADY)
        
        self.pause_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Pause for reference",
            command=self.pause_for_reference
        )
        self.pause_button.grid(row=7, column=0, columnspan=2, padx=PADX, pady=PADY)
        ToolTip(self.pause_button, "Pause scan to take dark and optional light references")
        self.continue_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Continue",
            command=self.continue_measurement,
            state=tk.DISABLED
        )
        self.continue_button.grid(row=7, column=2, columnspan=2, padx=PADX, pady=PADY)
        ToolTip(self.continue_button, "Return to saved position and resume scan")

        self.estimated_time_var = tk.StringVar(value="Estimated time: n/a")
        self.estimated_time_label = ttk.Label(
            self.wasatch_measure_frame, textvariable=self.estimated_time_var
        )
        self.estimated_time_label.grid(row=8, column=0, columnspan=4, padx=PADX, pady=PADY)

        # list of widgets disabled during a long scan
        self.disable_on_run = [
            self.connect_button,
            self.up_button,
            self.down_button,
            self.manual_command_button,
            self.zero_button,
            self.set_button_1,
            self.set_button_2,
            self.set_button_3,
            self.set_button_4,
            self.set_button_5,
            self.goto_button_1,
            self.goto_button_2,
            self.goto_button_3,
            self.goto_button_4,
            self.goto_button_5,
            self.test_button,
            self.init_button,
        ]

        # 3D head position preview
        self.map_frame = ttk.LabelFrame(self.plots_frame, text="Head position 3D", **FRAME_KWARGS)
        self.map_frame.pack(padx=PADX, pady=PADY)
        self.map_fig = Figure(figsize=(2.5, 2.5), dpi=100)
        self.map_ax = self.map_fig.add_subplot(111, projection='3d')
        try:
            self.map_ax.set_proj_type('ortho')
        except Exception:
            pass
        self.map_canvas = FigureCanvasTkAgg(self.map_fig, master=self.map_frame)
        self.map_canvas.draw()
        self.map_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=False)
        self.position_label = ttk.Label(self.map_frame, text="X:0 Y:0 Z:0")
        self.position_label.pack()
        zoom_frame = ttk.Frame(self.map_frame)
        zoom_frame.pack()
        ttk.Button(zoom_frame, text="🔍+", command=lambda: self.zoom_axes(self.map_ax, self.map_canvas, 0.8, True)).pack(side="left")
        ttk.Button(zoom_frame, text="🔍-", command=lambda: self.zoom_axes(self.map_ax, self.map_canvas, 1.25, True)).pack(side="left")
        self.head_dot = None
        self.volume_lines = []
        self.point_dots = []
        self.point_labels = []

        # Points preview under head position
        self.points_frame = ttk.LabelFrame(self.plots_frame, text="Scan points", **FRAME_KWARGS)
        self.points_frame.pack(padx=PADX, pady=PADY)
        self.wasatch.init_points_plot(self.points_frame)
        zoom_frame = ttk.Frame(self.points_frame)
        zoom_frame.pack()
        ttk.Button(zoom_frame, text="🔍+", command=lambda: self.zoom_axes(self.wasatch.points_ax, self.wasatch.points_canvas, 0.8, True)).pack(side="left")
        ttk.Button(zoom_frame, text="🔍-", command=lambda: self.zoom_axes(self.wasatch.points_ax, self.wasatch.points_canvas, 1.25, True)).pack(side="left")

        # Spectrum plot centered under measuring controls
        self.spectrum_frame = ttk.LabelFrame(
            self.controls_frame, text="Spectrum", **FRAME_KWARGS
        )
        self.spectrum_frame.pack(padx=PADX, pady=PADY, anchor='n')
        self.wasatch.init_spectrum_plot(self.spectrum_frame)
        zoom_frame = ttk.Frame(self.spectrum_frame)
        zoom_frame.pack()
        ttk.Button(
            zoom_frame,
            text="🔍+",
            command=lambda: self.zoom_axes(self.wasatch.ax, self.wasatch.canvas, 0.8),
        ).pack(side="left")
        ttk.Button(
            zoom_frame,
            text="🔍-",
            command=lambda: self.zoom_axes(self.wasatch.ax, self.wasatch.canvas, 1.25),
        ).pack(side="left")

        self.help_button = ttk.Button(self.right_frame, text="Help", command=self.show_help)
        self.help_button.grid(row=4, column=0, padx=PADX, pady=PADY, sticky="ew")

        self.calculate_predicted_points()

    def disable_controls(self):
        for w in self.disable_on_run:
            w.config(state=tk.DISABLED)
        for frame in [self.control_frame, self.step_speed_frame]:
            for child in frame.winfo_children():
                child.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)

    def enable_controls(self):
        for w in self.disable_on_run:
            w.config(state=tk.NORMAL)
        for frame in [self.control_frame, self.step_speed_frame]:
            for child in frame.winfo_children():
                child.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.continue_button.config(state=tk.DISABLED)
        self.wasatch_dark_button.config(state=tk.DISABLED)
        self.wasatch_light_button.config(state=tk.DISABLED)

    def show_help(self):
        from tkinter import messagebox
        msg = (
            "Troubleshooting:\n"
            "- Define positions with the SET buttons before running.\n"
            "- Scroll the window if some widgets are hidden.\n"
            "- Files are automatically numbered when existing.\n"
            "- Ensure matplotlib is installed for plotting.\n\n"
            "Workflow:\n"
            "- Use 'Run area' to scan the volume defined by your points.\n"
            "- During a scan, press 'Pause for reference' to take dark and light references.\n"
            "  Move the head, click 'Dark' and optionally 'Light', then 'Continue' to resume.\n"
            "- Hover over buttons for additional tips."
        )
        messagebox.showinfo("Help", msg)

    def update_progress(self, progress):
        def _do():
            self.progress_bar["value"] = progress
            self.progress_label['text'] = f"{progress} %"
        self.root.after(0, _do)

    def zoom_axes(self, ax, canvas, factor, is3d=False):
        if is3d:
            xlim = ax.get_xlim3d()
            ylim = ax.get_ylim3d()
            zlim = ax.get_zlim3d()
            xmid = sum(xlim) / 2
            ymid = sum(ylim) / 2
            zmid = sum(zlim) / 2
            xspan = (xlim[1] - xlim[0]) * factor / 2
            yspan = (ylim[1] - ylim[0]) * factor / 2
            zspan = (zlim[1] - zlim[0]) * factor / 2
            ax.set_xlim3d(xmid - xspan, xmid + xspan)
            ax.set_ylim3d(ymid - yspan, ymid + yspan)
            ax.set_zlim3d(zmid - zspan, zmid + zspan)
            ax.auto_scale_xyz(ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d())
            ax.set_box_aspect([1, 1, 1])
        else:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            xmid = sum(xlim) / 2
            ymid = sum(ylim) / 2
            xspan = (xlim[1] - xlim[0]) * factor / 2
            yspan = (ylim[1] - ylim[0]) * factor / 2
            ax.set_xlim(xmid - xspan, xmid + xspan)
            ax.set_ylim(ymid - yspan, ymid + yspan)
        canvas.draw_idle()
        if ax is self.map_ax:
            self.map_zoomed = True
        elif ax is self.wasatch.points_ax:
            self.wasatch.points_zoomed = True

    def pan_axes(self, ax, canvas, frac, axis='x'):
        if axis == 'x':
            lim = ax.get_xlim()
            span = lim[1] - lim[0]
            shift = span * frac
            ax.set_xlim(lim[0] + shift, lim[1] + shift)
        else:
            lim = ax.get_ylim()
            span = lim[1] - lim[0]
            shift = span * frac
            ax.set_ylim(lim[0] + shift, lim[1] + shift)
        canvas.draw_idle()

    def set_integration_time(self):
        integration_time = int(self.integration_time_entry.get())
        self.integration_time = integration_time
        self.wasatch.set_integration_time(integration_time)
        self.update_time_estimate()

    def set_scans_to_average(self):
        scans_to_average = int(self.scans_to_average_entry.get())
        self.scans_to_average = scans_to_average
        self.wasatch.set_scans_to_average(scans_to_average)
        self.update_time_estimate()

    def set_boxcar_half_width(self):
        boxcar_half_width = int(self.boxcar_half_width_entry.get())
        self.wasatch.set_boxcar_half_width(boxcar_half_width)

    def set_delay_ms(self):
        delay_ms = int(self.delay_ms_entry.get())
        self.delay_ms = delay_ms
        self.wasatch.set_delay_ms(delay_ms)
        self.update_time_estimate()

    def set_max_spectra(self):
        max_spectra = int(self.max_spectra_entry.get())
        self.wasatch.set_max_spectra(max_spectra)

    def browse_file_path(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if file_path:
            self.file_path_entry.delete(0, tk.END)
            self.file_path_entry.insert(0, file_path)
            self.wasatch.set_output_file_path(file_path)

    def ensure_file_path(self):
        if self.file_path_entry.get():
            return
        import os, random
        default_dir = os.path.join(os.getcwd(), "NIRv2_Witek")
        os.makedirs(default_dir, exist_ok=True)
        base = os.path.join(default_dir, "scan")
        path = f"{base}.csv"
        counter = 1
        while os.path.exists(path):
            path = f"{base}_{counter}.csv"
            counter += 1
        self.file_path_entry.insert(0, path)
        self.wasatch.set_output_file_path(path)

    def calculate_predicted_points(self):
        try:
            count_x = int(self.wasatch_samples_countX_entry.get())
            count_y = int(self.wasatch_samples_countY_entry.get())
            count_z = int(self.wasatch_samples_countZ_entry.get())
        except ValueError:
            return

        origin = self.user_positions.get('1')
        if not origin:
            self.wasatch.scan_points = self.user_positions
            self.wasatch.points_zoomed = False
            self.wasatch.update_points_plot()
            self.estimated_time_var.set("Estimated time: n/a")
            return

        x1 = origin['X']
        y1 = origin['Y']
        z1 = origin['Z']
        x2 = self.user_positions['2']['X'] if self.user_positions['2'] else x1
        y2 = self.user_positions['4']['Y'] if self.user_positions['4'] else y1
        z2 = self.user_positions['5']['Z'] if self.user_positions['5'] else z1

        if x1 == x2 and count_x > 1:
            self.wasatch_samples_countX_entry.delete(0, tk.END)
            self.wasatch_samples_countX_entry.insert(0, '1')
            count_x = 1
        if y1 == y2 and count_y > 1:
            self.wasatch_samples_countY_entry.delete(0, tk.END)
            self.wasatch_samples_countY_entry.insert(0, '1')
            count_y = 1
        if z1 == z2 and count_z > 1:
            self.wasatch_samples_countZ_entry.delete(0, tk.END)
            self.wasatch_samples_countZ_entry.insert(0, '1')
            count_z = 1
        try:
            self.wasatch.points_zoomed = False
            self.wasatch.set_scan_bounds(
                x1, x2,
                y1, y2,
                z1, z2,
                self.user_positions,
                count_x,
                count_y,
                count_z,
                progress_cb=self.update_progress,
                log_cb=self.log,
            )
            self.log("Points ready")
            self.update_time_estimate()
        except Exception:
            self.wasatch.scan_points = self.user_positions
            self.wasatch.update_points_plot()

    def update_time_estimate(self):
        try:
            count_x = int(self.wasatch_samples_countX_entry.get())
            count_y = int(self.wasatch_samples_countY_entry.get())
            count_z = int(self.wasatch_samples_countZ_entry.get())
        except ValueError:
            self.estimated_time_var.set("Estimated time: n/a")
            return

        defined = [p for p in self.user_positions.values() if p]
        if len(defined) < 2:
            self.estimated_time_var.set("Estimated time: n/a")
            return

        if self.wasatch.bounds:
            start_x, end_x, start_y, end_y, start_z, end_z = (
                self.wasatch.bounds[0], self.wasatch.bounds[1],
                self.wasatch.bounds[2], self.wasatch.bounds[3],
                self.wasatch.bounds[4], self.wasatch.bounds[5],
            )
        else:
            start_x = self.user_positions['1']['X']
            start_y = self.user_positions['1']['Y']
            start_z = self.user_positions['1']['Z']
            end_x = self.user_positions['2']['X'] if self.user_positions['2'] else start_x
            end_y = self.user_positions['4']['Y'] if self.user_positions['4'] else start_y
            end_z = self.user_positions['5']['Z'] if self.user_positions['5'] else start_z
        if start_x == end_x and count_x > 1:
            self.wasatch_samples_countX_entry.delete(0, tk.END)
            self.wasatch_samples_countX_entry.insert(0, '1')
            count_x = 1
        if start_y == end_y and count_y > 1:
            self.wasatch_samples_countY_entry.delete(0, tk.END)
            self.wasatch_samples_countY_entry.insert(0, '1')
            count_y = 1
        if start_z == end_z and count_z > 1:
            self.wasatch_samples_countZ_entry.delete(0, tk.END)
            self.wasatch_samples_countZ_entry.insert(0, '1')
            count_z = 1

        step_x = (end_x - start_x) / (count_x - 1) if count_x > 1 else 0
        step_y = (end_y - start_y) / (count_y - 1) if count_y > 1 else 0
        step_z = (end_z - start_z) / (count_z - 1) if count_z > 1 else 0

        self.log(
            f"Estimating time with counts X={count_x}, Y={count_y}, Z={count_z}"
        )
        self.log(
            f"Step sizes for estimate: step_x={step_x}, step_y={step_y}, step_z={step_z}"
        )

        measure_count = max(count_x * count_y * count_z, 1)
        move_delay = self.interpolate_time(max(abs(step_x), abs(step_y), abs(step_z)))
        per_point = self.integration_time * self.scans_to_average + self.delay_ms + move_delay
        total_ms = measure_count * per_point
        self.log(
            "Per point: integration=%dms x%d, delay=%dms, move=%dms -> %dms"
            % (self.integration_time, self.scans_to_average, self.delay_ms, move_delay, per_point)
        )
        # with absolute moves there is no separate row reset delay
        self.log(f"Estimated total time {total_ms/1000:.2f} s")

        self.estimated_time_var.set(
            f"Estimated time: {total_ms/1000:.1f} s"
        )

    def connect_spectrometer(self):
        self.log("Connecting spectrometer...")
        device = self.wasatch.connect()
        if device:
            self.log("Spectrometer connected")
        else:
            self.log("No spectrometer found")

    def toggle_connection(self):
        if self.serial.connected:
            log_message = self.serial.disconnect_cnc()
            self.connect_button.config(text="Connect")
        else:
            port = self.serial_port_combobox.get()
            log_message = self.serial.connect_cnc(port)
            self.connect_button.config(text="Disconnect")
        self.log(log_message)

    def log(self, message):
        def _do():
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
        self.root.after(0, _do)

    def prompt_retry(self, msg):
        """Show a retry/cancel dialog from the main thread and return True to retry."""
        q = queue.Queue()

        def _ask():
            q.put(messagebox.askretrycancel("Measurement error", msg))

        self.root.after(0, _ask)
        return q.get()

    def move(self, direction):
        step = float(self.get_step())
        speed = self.get_speed()

        if direction == '\u2191':  # Front (Y+)
            self.current_position['Y'] += step
            command = f'G1 Y{step} F{speed}'
        elif direction == '\u2193':  # Rear (Y-)
            self.current_position['Y'] -= step
            command = f'G1 Y-{step} F{speed}'
        elif direction == '\u2190':  # Left (X+)
            self.current_position['X'] += step
            command = f'G1 X{step} F{speed}'
        elif direction == '\u2192':  # Right (X-)
            self.current_position['X'] -= step
            command = f'G1 X-{step} F{speed}'
        elif direction == 'Up':  # Up (Z+)
            self.current_position['Z'] += step
            command = f'G1 Z{step} F{speed}'
        elif direction == 'Down':  # Down (Z-)
            self.current_position['Z'] -= step
            command = f'G1 Z-{step} F{speed}'
        else:
            command = ''

        if command:
            log_message = self.serial.send_gcode('G91')
            log_message = self.serial.send_gcode(command)
            self.log(log_message)
            log_message = self.serial.send_gcode('G90')
        self.update_map_position(
            self.current_position['X'],
            self.current_position['Y'],
            self.current_position['Z']
        )

    def get_step(self):
        return self.step_entry.get()

    def get_speed(self):
        return self.speed_entry.get()

    
    def send_manual_command(self):
        command = self.manual_command_entry.get()
        log_message = self.serial.send_gcode(command)
        self.log(log_message)

    def update_map_position(self, x, y, z=None):
        if z is None:
            z = self.current_position['Z']

        def _do():
            self.position_label.config(text=f"X:{x:.1f} Y:{y:.1f} Z:{z:.1f}")

            if self.head_dot:
                self.head_dot.remove()
            self.head_dot = self.map_ax.scatter([x], [y], [z], c='red', s=80, alpha=0.6)

            self.wasatch.position = (x, y, z)
            self.update_volume_display()
            self.map_canvas.draw()
            self.wasatch.update_points_plot()

        self.root.after(0, _do)

    def update_volume_display(self):
        import itertools

        # clear previous drawings
        for line in self.volume_lines:
            line.remove()
        self.volume_lines = []
        for dot in self.point_dots:
            dot.remove()
        self.point_dots = []
        for label in self.point_labels:
            label.remove()
        self.point_labels = []

        # draw reference points if any
        colors = ['blue', 'green', 'magenta', 'orange', 'cyan']
        xs = []
        ys = []
        zs = []
        for idx, key in enumerate(['1','2','3','4','5']):
            pt = self.user_positions.get(key)
            if pt:
                xs.append(pt['X'])
                ys.append(pt['Y'])
                zs.append(pt['Z'])
                dot = self.map_ax.scatter([pt['X']], [pt['Y']], [pt['Z']], color=colors[idx], marker='o')
                label = self.map_ax.text(pt['X'], pt['Y'], pt['Z'], f"{key}")
                self.point_dots.append(dot)
                self.point_labels.append(label)

        # draw volume if all bounding points are set
        if all(self.user_positions.get(k) for k in ['1','2','4','5']):
            x1 = self.user_positions['1']['X']
            x2 = self.user_positions['2']['X']
            y1 = self.user_positions['1']['Y']
            y2 = self.user_positions['4']['Y']
            z1 = self.user_positions['1']['Z']
            z2 = self.user_positions['5']['Z']
            xs.extend([x1, x2])
            ys.extend([y1, y2])
            zs.extend([z1, z2])
            corners = list(itertools.product([x1, x2], [y1, y2], [z1, z2]))
            edges = [
                (0,1),(0,2),(2,3),(1,3),
                (4,5),(4,6),(6,7),(5,7),
                (0,4),(1,5),(2,6),(3,7)
            ]
            for e in edges:
                line = self.map_ax.plot(
                    [corners[e[0]][0], corners[e[1]][0]],
                    [corners[e[0]][1], corners[e[1]][1]],
                    [corners[e[0]][2], corners[e[1]][2]],
                    color='black'
                )[0]
                self.volume_lines.append(line)

        if xs and ys and zs and not self.map_zoomed:
            self.map_ax.set_xlim(min(xs), max(xs))
            self.map_ax.set_ylim(min(ys), max(ys))
            self.map_ax.set_zlim(min(zs), max(zs))
        self.map_ax.set_box_aspect([1, 1, 1])
        self.map_ax.auto_scale_xyz(
            self.map_ax.get_xlim3d(),
            self.map_ax.get_ylim3d(),
            self.map_ax.get_zlim3d(),
        )
        self.map_ax.invert_zaxis()
        self.map_ax.set_xlabel('X')
        self.map_ax.set_ylabel('Y')
        self.map_ax.set_zlabel('Z')


    def set_position(self, position_number):
        self.map_zoomed = False
        self.user_positions[str(position_number)] = self.current_position.copy()
        self.log(
            f"Position {position_number} set to X:{self.current_position['X']}, Y:{self.current_position['Y']}, Z:{self.current_position['Z']}."
        )
        self.update_map_position(
            self.current_position['X'],
            self.current_position['Y'],
            self.current_position['Z']
        )
        self.calculate_predicted_points()

    def goto_position(self, position_number):
        pos = self.user_positions.get(str(position_number))
        if not pos:
            self.log(f"Position {position_number} not set")
            return
        # move relative to current position to avoid running beyond limits
        delta_x = pos['X'] - self.current_position['X']
        delta_y = pos['Y'] - self.current_position['Y']
        delta_z = pos['Z'] - self.current_position['Z']

        self.serial.send_gcode('G91')
        cmd = (
            f"G1 X{ delta_x } Y{ delta_y } Z{ delta_z } F{self.get_speed()}"
        )
        self.serial.send_gcode(cmd)
        self.waitForCNC()
        self.serial.send_gcode('G90')
        self.current_position = pos.copy()
        self.update_map_position(pos['X'], pos['Y'], pos['Z'])
        self.log(f"Moved to position {position_number}")

    def test_positions(self):
        """Move along all edges of the defined volume."""
        try:
            import itertools
            x1 = self.user_positions['1']['X']
            x2 = self.user_positions['2']['X']
            y1 = self.user_positions['1']['Y']
            y2 = self.user_positions['4']['Y']
            z1 = self.user_positions['1']['Z']
            z2 = self.user_positions['5']['Z']
        except Exception:
            self.log('Set positions 1,2,4 and 5 first')
            return

        xs = [x1, x2]
        ys = [y1, y2]
        zs = [z1, z2]
        corners = list(itertools.product(xs, ys, zs))
        edges = [
            (0,1),(0,2),(2,3),(1,3),
            (4,5),(4,6),(6,7),(5,7),
            (0,4),(1,5),(2,6),(3,7)
        ]

        self.serial.send_gcode('G90')
        for e in edges:
            target = corners[e[1]]
            cmd = (
                f"G1 X{ target[0] } Y{ target[1] } Z{ target[2] } F{self.get_speed()}"
            )
            self.serial.send_gcode(cmd)
            self.log(f"Moving to {target}")
            self.waitForCNC()
            self.update_map_position(target[0], target[1], target[2])
        self.serial.send_gcode('G91')

    def set_position_zero(self):
        self.current_position = {'X': 0, 'Y': 0, 'Z': 0}
        self.serial.send_gcode('G92 X0 Y0 Z0')
        self.log("Current position set as 0,0,0.")
        self.update_map_position(0, 0, 0)

    def move_to_zero(self):
        init_commands = [
            'G90',
            'G1 X0 Y0 Z0',
            'G91',
        ]
        for cmd in init_commands:
            self.log(self.serial.send_gcode(cmd))
            # time.sleep(1)

        self.log("Complete.")
        self.update_map_position(0, 0, 0)

    def start_measurement(self):
        self.stop_measurement()

        if not self.running:
            defined = [k for k, v in self.user_positions.items() if v]
            if len(defined) < 2:
                self.log('Set at least two positions first')
                return
            if not self.serial.connected:
                self.log('CNC not connected')
                return
            self.calculate_predicted_points()
            self.ensure_file_path()
            self.update_time_estimate()
            self.running = True
            self.disable_controls()
            self.log("Starting run-area measurement")
            self.log(self.estimated_time_var.get())
            # New thread for measuring
            if not self.measure_thread or not self.measure_thread.is_alive():
                self.measure_thread = threading.Thread(target=self.measure_and_move)
                self.measure_thread.start()  # Run thread

    def start_measurement_once(self):
        run_once_string = self.run_once_description.get()
        if run_once_string == '':
            run_once_string = 'single measure'
        self.ensure_file_path()
        self.wasatch.init_file()
        self.log(run_once_string)
        self.wasatch.position = (None, None, None)
        finished = False
        while True:
            finished = self.wasatch.run(run_once_string)
            if finished:
                break
            self.log("Single measurement failed, prompting user")
            if not self.prompt_retry("Measurement failed. Retry?"):
                break
        self.wasatch.close_file()
        if not finished:
            self.log("Single measurement aborted")
        self.enable_controls()
        return

    def stop_measurement(self):
        self.running = False
        self.paused = False
        self.dark_taken = False
        self.enable_controls()
        self.update_progress(0)
        self.wasatch.points = []
        self.wasatch.update_points_plot()
        self.map_zoomed = False
        self.wasatch.points_zoomed = False
        if getattr(self.serial, 'connected', False):
            self.serial.send_gcode('G91')

    def measure_and_move(self):
        self.update_progress(0)

        self.samples_count_x = int(self.wasatch_samples_countX_entry.get())
        self.samples_count_y = int(self.wasatch_samples_countY_entry.get())
        self.samples_count_z = int(self.wasatch_samples_countZ_entry.get())

        x1, x2, y1, y2, z1, z2 = self.wasatch.bounds
        start_x, start_y, start_z = x1, y1, z1
        end_x, end_y, end_z = x2, y2, z2

        if x1 == x2:
            self.samples_count_x = 1
        if y1 == y2:
            self.samples_count_y = 1
        if z1 == z2:
            self.samples_count_z = 1

        step_x = (end_x - start_x) / (self.samples_count_x - 1) if self.samples_count_x > 1 else 0
        step_y = (end_y - start_y) / (self.samples_count_y - 1) if self.samples_count_y > 1 else 0
        step_z = (end_z - start_z) / (self.samples_count_z - 1) if self.samples_count_z > 1 else 0

        self.log(
            f"Bounds: x1={start_x}, x2={end_x}, y1={start_y}, y2={end_y}, z1={start_z}, z2={end_z}"
        )
        self.log(
            f"Step sizes: step_x={step_x}, step_y={step_y}, step_z={step_z}"
        )

        # ensure absolute coordinates and move to starting point
        self.serial.send_gcode('G90')
        start_cmd = (
            f"G1 X{start_x} Y{start_y} Z{start_z} F{self.get_speed()}"
        )
        self.serial.send_gcode(start_cmd)
        self.log(
            f"Moving to start position X: {start_x}, Y: {start_y}, Z: {start_z}"
        )
        self.waitForCNC()
        self.current_position = {'X': start_x, 'Y': start_y, 'Z': start_z}
        self.update_map_position(start_x, start_y, start_z)

        total_points = self.samples_count_x * self.samples_count_y * self.samples_count_z
        self.log(f"Total scan points: {total_points}")
        self.wasatch.points = []
        self.wasatch.update_points_plot()
        current_measure = 0
        measure_count = total_points

        self.wasatch.init_file()
        self.log(f"Output file: {self.wasatch.args.outfile}")

        while self.running:
            for k in range(self.samples_count_z):
                abs_z = start_z + k * step_z
                self.log(f"Z layer {k+1}/{self.samples_count_z} at Z={abs_z}")
                for i in range(self.samples_count_x):
                    abs_x = start_x + i * step_x
                    self.log(f"  X step {i+1}/{self.samples_count_x} at X={abs_x}")
                    for j in range(self.samples_count_y):
                        if not self.running:
                            break
                        while self.paused and self.running:
                            time.sleep(0.1)
                        abs_y = start_y + j * step_y
                        self.log(f"    Y step {j+1}/{self.samples_count_y} at Y={abs_y}")
                        move_command = (
                            f"G1 X{abs_x} Y{abs_y} Z{abs_z} F{self.get_speed()}"
                        )
                        self.serial.send_gcode(move_command)
                        self.log(
                            f"Moving to position X: {abs_x}, Y: {abs_y}, Z: {abs_z}"
                        )
                        self.waitForCNC()
                        dx = abs(abs_x - self.current_position['X'])
                        dy = abs(abs_y - self.current_position['Y'])
                        dz = abs(abs_z - self.current_position['Z'])
                        max_step = max(dx, dy, dz)
                        self.measureDelayFromSteps(max_step)
                        self.current_position = {'X': abs_x, 'Y': abs_y, 'Z': abs_z}
                        self.update_map_position(abs_x, abs_y, abs_z)

                        success = False
                        attempts = 0
                        max_attempts = 3
                        while self.running and not success:
                            finished = self.wasatch.run_with_position("scan", abs_x, abs_y, abs_z)
                            if finished:
                                success = True
                            else:
                                attempts += 1
                                self.log(
                                    f"Measurement attempt {attempts} failed at X:{abs_x} Y:{abs_y} Z:{abs_z}"
                                )
                                # try to auto-reconnect before retrying
                                self.wasatch.connect()
                                if attempts < max_attempts:
                                    self.log("Automatically retrying measurement")
                                    continue
                                self.log("Max auto-retries exceeded")
                                if not self.prompt_retry(
                                    f"Measurement failed at X:{abs_x} Y:{abs_y} Z:{abs_z}. Retry?"
                                ):
                                    self.running = False
                                    break
                                attempts = 0

                        if not self.running:
                            break

                        current_measure += 1
                        progress = int((current_measure / measure_count) * 100)
                        self.update_progress(progress)
                        self.log(f"Measure {current_measure} out of {measure_count}.")

                    if not self.running:
                        self.log("Stopped.")
                        break

            self.serial.send_gcode('G91')
            self.running = False

        expected_lines = self.samples_count_x * self.samples_count_y * self.samples_count_z
        if self.wasatch.outfile:
            self.wasatch.outfile.flush()
            try:
                with open(self.wasatch.args.outfile, 'r') as fh:
                    written = sum(1 for _ in fh) - 1
                if written != expected_lines:
                    self.log(
                        f"Warning: expected {expected_lines} points, but file has {written}"
                    )
                else:
                    self.log(f"CSV contains {written} lines as expected")
            except Exception as exc:
                self.log(f"Failed to verify CSV completeness: {exc}")

    def waitForCNC(self):
        self.log("Waiting for CNC to finish move")
        while not self.serial.wait_for_ending_move():
            time.sleep(0.01)

    def measureDelayFromSteps(self, step):
        wait_time_ms = self.interpolate_time(step) + 100
        self.log(f"Delaying {wait_time_ms} ms for step {step}")
        time.sleep(wait_time_ms/1000)

    def interpolate_time(self, step):
        steps = np.array([1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 100, 120, 150])
        times = np.array([500, 1150, 1560, 2000, 2700, 3300, 3800, 4400, 4900, 5500, 6600, 7800, 9700])
        if step <= steps[0]:
            return times[0]
        else:
            return np.interp(step, steps, times)

    def run_dark(self):
        if not self.paused:
            self.log("Pause measurement before taking dark reference")
            return
        self.ensure_file_path()
        if self.wasatch.outfile is None or self.wasatch.outfile.closed:
            self.wasatch.init_file()
        self.log("Dark measure")
        pos = (
            self.current_position['X'],
            self.current_position['Y'],
            self.current_position['Z'],
        )
        attempts = 0
        max_attempts = 3
        while True:
            finished = self.wasatch.run_with_position("dark", *pos)
            if finished:
                break
            attempts += 1
            self.log(f"Dark measurement attempt {attempts} failed")
            self.wasatch.connect()
            if attempts < max_attempts:
                self.log("Automatically retrying dark measurement")
                continue
            self.log("Max auto-retries for dark measurement exceeded")
            if not self.prompt_retry("Dark measurement failed. Retry?"):
                self.log("Dark measurement aborted")
                return
            attempts = 0
        self.dark_taken = True
        self.continue_button.config(state=tk.NORMAL)

    def run_light(self):
        if not self.paused:
            self.log("Pause measurement before taking light reference")
            return
        self.ensure_file_path()
        if self.wasatch.outfile is None or self.wasatch.outfile.closed:
            self.wasatch.init_file()
        self.log("Light measure")
        pos = (
            self.current_position['X'],
            self.current_position['Y'],
            self.current_position['Z'],
        )
        attempts = 0
        max_attempts = 3
        while True:
            finished = self.wasatch.run_with_position("light", *pos)
            if finished:
                break
            attempts += 1
            self.log(f"Light measurement attempt {attempts} failed")
            self.wasatch.connect()
            if attempts < max_attempts:
                self.log("Automatically retrying light measurement")
                continue
            self.log("Max auto-retries for light measurement exceeded")
            if not self.prompt_retry("Light measurement failed. Retry?"):
                self.log("Light measurement aborted")
                return
            attempts = 0

    def pause_for_reference(self):
        if not self.running:
            self.log("Nothing to pause")
            return
        self.paused = True
        self.dark_taken = False
        self.continue_button.config(state=tk.DISABLED)
        self.pause_position = self.current_position.copy()
        self.wasatch_dark_button.config(state=tk.NORMAL)
        self.wasatch_light_button.config(state=tk.NORMAL)
        for frame in [self.control_frame, self.step_speed_frame]:
            for child in frame.winfo_children():
                child.config(state=tk.NORMAL)
        self.log("Paused. Take dark reference and optionally light, then press Continue")

    def continue_measurement(self):
        if not self.paused:
            return
        if not self.dark_taken:
            self.log("Take dark reference before continuing")
            return
        # return to stored pause position
        target = self.pause_position
        move_cmd = f"G1 X{target['X']} Y{target['Y']} Z{target['Z']} F{self.get_speed()}"
        self.serial.send_gcode('G90')
        self.serial.send_gcode(move_cmd)
        self.waitForCNC()
        self.current_position = target.copy()
        self.update_map_position(target['X'], target['Y'], target['Z'])
        self.paused = False
        self.continue_button.config(state=tk.DISABLED)
        self.wasatch_dark_button.config(state=tk.DISABLED)
        self.wasatch_light_button.config(state=tk.DISABLED)
        for frame in [self.control_frame, self.step_speed_frame]:
            for child in frame.winfo_children():
                child.config(state=tk.DISABLED)
        self.log("Resuming measurement")
