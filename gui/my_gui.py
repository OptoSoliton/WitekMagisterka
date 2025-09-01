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

# color palettes for light and dark modes
LIGHT_BG = '#f0f0f0'
LIGHT_FG = '#000000'

# Dark theme palette inspired by GitHub's dark mode
APP_BG = '#0F1117'
CARD_BG = '#161B22'
INPUT_BG = '#1F2937'
BORDER_COLOR = '#2A2F3A'
TEXT_PRIMARY = '#E6E8EB'
ACCENT = '#22D3EE'
PROGRESS_FILL = '#22D3EE'
PROGRESS_BG = '#2A2F3A'
GRID_COLOR = '#2A2F3A'
AXIS_COLOR = '#D1D5DB'
LINE_COLORS = ['#60A5FA', '#34D399', '#F472B6', '#A78BFA',
               '#F59E0B', '#F87171', '#22D3EE', '#93C5FD']


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
        self.container_window = self.canvas.create_window((0, 0), window=self.container, anchor='nw')
        self.container.bind(
            '<Configure>',
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        )
        self.canvas.bind(
            '<Configure>',
            lambda e: self.canvas.itemconfigure(self.container_window, width=e.width)
        )

        # allow right frame to expand more than left and disable stray third column
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_columnconfigure(1, weight=2)
        self.container.grid_columnconfigure(2, weight=0)
        self.container.grid_rowconfigure(0, weight=1)

        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.dark_mode = False

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
        self.marked_positions = []
        self.marked_dots_head = []
        self.marked_labels_head = []

        self.setup_ui()
        self.apply_theme()
        self.wasatch.set_dark_mode(self.dark_mode)
        # forward Wasatch logger messages to GUI log
        handler = TkLogHandler(self)
        self.wasatch.set_logger_handler(handler)
        self.running = False
        self.paused = False
        self.measure_thread = threading.Thread()
        self.pause_position = None

        self.auto_update_map()

    def setup_ui(self):

        # Left and right part of window inside scrollable container
        self.left_frame = ttk.Frame(self.container)
        self.left_frame.grid(row=0, column=0, sticky='nw')
        self.left_frame.columnconfigure(0, weight=1)
        self.left_frame.columnconfigure(1, weight=0, minsize=260)
        self.left_frame.columnconfigure(2, weight=0)


        # prawa kolumna: kontrolki na górze, poniżej 2 szerokie panele wykresów
        self.right_frame = ttk.Frame(self.container)
        self.right_frame.grid(row=0, column=1, sticky='nsew')
        self.right_frame.grid_columnconfigure(0, weight=1)
        self.right_frame.grid_rowconfigure(0, weight=0)  # controls
        self.right_frame.grid_rowconfigure(1, weight=1)  # plots


        self.controls_frame = ttk.Frame(self.right_frame)
        self.controls_frame.grid(row=0, column=0, sticky='ew')

        self.plots_frame = ttk.Frame(self.right_frame)
        self.plots_frame.grid(row=1, column=0, sticky='nsew')
        self.plots_frame.grid_columnconfigure(0, weight=1)
        self.plots_frame.grid_columnconfigure(1, weight=1)
        self.plots_frame.grid_rowconfigure(0, weight=1)


        # Serial connection frame
        self.serial_connection_frame = ttk.LabelFrame(self.left_frame, text="CNC serial connection", **FRAME_KWARGS)
        self.serial_connection_frame.grid(row=0, column=0, padx=PADX, pady=PADY, sticky="ew")

        self.serial_port_label = ttk.Label(self.serial_connection_frame, text="Port COM:")
        self.serial_port_label.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.serial_ports = self.serial.list_serial_ports()
        self.serial_port_combobox = ttk.Combobox(self.serial_connection_frame, values=self.serial_ports)
        self.serial_port_combobox.grid(row=0, column=1, padx=PADX, pady=PADY)

        self.connect_button = ttk.Button(self.serial_connection_frame, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # Control frame for movement
        self.control_frame = ttk.LabelFrame(self.left_frame, text="CNC control", **FRAME_KWARGS)
        self.control_frame.grid(row=1, column=0, padx=PADX, pady=PADY, sticky="ew")

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
        self.step_speed_frame.grid(row=2, column=0, padx=PADX, pady=PADY, sticky="ew")

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
        self.position_frame.grid(row=3, column=0, padx=PADX, pady=PADY, sticky="ew")

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
        self.manual_command_frame.grid(row=4, column=0, padx=PADX, pady=PADY, sticky="ew")

        self.manual_command_label = ttk.Label(self.manual_command_frame, text="Command:")
        self.manual_command_label.grid(row=0, column=0, padx=PADX, pady=PADY)

        self.manual_command_entry = ttk.Entry(self.manual_command_frame, width=30)
        self.manual_command_entry.grid(row=0, column=1,  padx=PADX, pady=PADY)

        self.manual_command_button = ttk.Button(self.manual_command_frame, text="Send", command=self.send_manual_command)
        self.manual_command_button.grid(row=0, column=2, padx=PADX, pady=PADY)

        # --- DÓŁ LEWEJ KOLUMNY: Spectrum + Marked positions obok siebie ---
        self.bottom_row = ttk.Frame(self.left_frame)
        self.bottom_row.grid(row=5, column=0, sticky='nsew')
        self.left_frame.rowconfigure(5, weight=1)
        self.bottom_row.columnconfigure(0, weight=1)
        self.bottom_row.columnconfigure(1, weight=0, minsize=260)
        self.bottom_row.rowconfigure(0, weight=1)

        # Spectrum (po lewej)
        self.spectrum_frame = ttk.LabelFrame(self.bottom_row, text="Spectrum", **FRAME_KWARGS)
        self.spectrum_frame.grid(row=0, column=0, padx=PADX, pady=PADY, sticky='nsew')
        spec_container = ttk.Frame(self.spectrum_frame)
        spec_container.pack(side='top', fill='both', expand=True)
        self.wasatch.init_spectrum_plot(spec_container)

        nav_frame = ttk.Frame(self.spectrum_frame)
        nav_frame.pack(side='bottom', fill='x')
        buttons = [
            ("◀", lambda: self.pan_axes(self.wasatch.ax, self.wasatch.canvas, -0.1, axis='x')),
            ("▶", lambda: self.pan_axes(self.wasatch.ax, self.wasatch.canvas, 0.1, axis='x')),
            ("▲", lambda: self.pan_axes(self.wasatch.ax, self.wasatch.canvas, 0.1, axis='y')),
            ("▼", lambda: self.pan_axes(self.wasatch.ax, self.wasatch.canvas, -0.1, axis='y')),
            ("🔍+", lambda: self.zoom_axes(self.wasatch.ax, self.wasatch.canvas, 0.8)),
            ("🔍-", lambda: self.zoom_axes(self.wasatch.ax, self.wasatch.canvas, 1.25)),
        ]
        for i, (text, cmd) in enumerate(buttons):
            ttk.Button(nav_frame, text=text, command=cmd).grid(row=0, column=i, padx=PADX, pady=(PADY, 0))
            nav_frame.grid_columnconfigure(i, weight=1)

        # Marked positions (po prawej)
        self.marks_frame = ttk.LabelFrame(self.bottom_row, text="Marked positions", **FRAME_KWARGS)
        self.marks_frame.grid(row=0, column=1, padx=PADX, pady=PADY, sticky='nsew')

        self.marked_list = tk.Listbox(self.marks_frame)
        self.marked_list.pack(padx=PADX, pady=PADY, fill='both', expand=True)
        self.delete_mark_button = ttk.Button(self.marks_frame, text="Delete marked point", command=self.delete_marked_point)
        self.delete_mark_button.pack(padx=PADX, pady=(0, PADY))

        # Log – zwężony i dociągnięty do dołu
        self.log_frame = ttk.LabelFrame(self.left_frame, text="Log", **FRAME_KWARGS)
        self.log_frame.grid(row=0, column=1, rowspan=6, padx=PADX, pady=PADY, sticky='ns')
        self.log_frame.configure(width=260)

        self.log_text = scrolledtext.ScrolledText(self.log_frame, wrap=tk.WORD, height=22, width=26)

        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Spectrometer connection and file path
        self.io_frame = ttk.LabelFrame(self.controls_frame, text="Wasatch I/O", **FRAME_KWARGS)
        self.io_frame.pack(fill='x', padx=PADX, pady=1)

        self.wasatch_connect_button = ttk.Button(
            self.io_frame, text="Connect spectrometer", command=self.connect_spectrometer
        )
        self.wasatch_connect_button.grid(row=0, column=0, padx=PADX, pady=PADY, sticky="w")

        self.file_path_entry = ttk.Entry(self.io_frame, width=30)
        self.file_path_entry.grid(row=0, column=1, padx=PADX, pady=PADY, sticky="ew")

        self.file_path_button = ttk.Button(self.io_frame, text="Browse", command=self.browse_file_path)
        self.file_path_button.grid(row=0, column=2, padx=PADX, pady=PADY)


        self.io_frame.grid_columnconfigure(1, weight=1)

        # Generate default output path on startup
        self.ensure_file_path()

        # Wasatch parameters, arranged in two compact rows
        self.wasatch_parameters_frame = ttk.LabelFrame(self.controls_frame, text="Parameters", **FRAME_KWARGS)
        self.wasatch_parameters_frame.pack(fill='x', padx=PADX, pady=1)

        # first row: integration time and scans to average
        self.integration_time_label = ttk.Label(self.wasatch_parameters_frame, text="Integration Time (ms):")
        self.integration_time_label.grid(row=0, column=0, padx=PADX, pady=(PADY, 0), sticky="w")
        self.integration_time_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.integration_time_entry.grid(row=0, column=1, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.integration_time_entry.insert(tk.END, str(self.integration_time))
        self.set_integration_time_button = ttk.Button(
            self.wasatch_parameters_frame,
            text="Set",
            command=self.set_integration_time,
            width=4,
        )
        self.set_integration_time_button.grid(row=0, column=2, padx=PADX, pady=(PADY, 0))

        self.scans_to_average_label = ttk.Label(self.wasatch_parameters_frame, text="Scans to Average:")
        self.scans_to_average_label.grid(row=0, column=3, padx=PADX, pady=(PADY, 0), sticky="w")
        self.scans_to_average_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.scans_to_average_entry.grid(row=0, column=4, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.scans_to_average_entry.insert(tk.END, str(self.scans_to_average))
        self.set_scans_to_average_button = ttk.Button(
            self.wasatch_parameters_frame,
            text="Set",
            command=self.set_scans_to_average,
            width=4,
        )
        self.set_scans_to_average_button.grid(row=0, column=5, padx=PADX, pady=(PADY, 0))

        # second row: boxcar, delay and max spectra
        self.boxcar_half_width_label = ttk.Label(self.wasatch_parameters_frame, text="Boxcar half width:")
        self.boxcar_half_width_label.grid(row=1, column=0, padx=PADX, pady=(PADY, PADY), sticky="w")
        self.boxcar_half_width_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.boxcar_half_width_entry.grid(row=1, column=1, padx=PADX, pady=(PADY, PADY), sticky="ew")
        self.boxcar_half_width_entry.insert(tk.END, str(self.boxcar_half_width))
        self.set_boxcar_half_width_button = ttk.Button(
            self.wasatch_parameters_frame,
            text="Set",
            command=self.set_boxcar_half_width,
            width=4,
        )
        self.set_boxcar_half_width_button.grid(row=1, column=2, padx=PADX, pady=(PADY, PADY))

        self.delay_ms_label = ttk.Label(self.wasatch_parameters_frame, text="Delay (ms):")
        self.delay_ms_label.grid(row=1, column=3, padx=PADX, pady=(PADY, PADY), sticky="w")
        self.delay_ms_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.delay_ms_entry.grid(row=1, column=4, padx=PADX, pady=(PADY, PADY), sticky="ew")
        self.delay_ms_entry.insert(tk.END, str(self.delay_ms))
        self.set_delay_ms_button = ttk.Button(
            self.wasatch_parameters_frame,
            text="Set",
            command=self.set_delay_ms,
            width=4,
        )
        self.set_delay_ms_button.grid(row=1, column=5, padx=PADX, pady=(PADY, PADY))

        # third row: max spectra
        self.max_spectra_label = ttk.Label(self.wasatch_parameters_frame, text="Max Spectra:")
        self.max_spectra_label.grid(row=1, column=6, padx=PADX, pady=(PADY, PADY), sticky="w")
        self.max_spectra_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.max_spectra_entry.grid(row=1, column=7, padx=PADX, pady=(PADY, PADY), sticky="ew")
        self.max_spectra_entry.insert(tk.END, str(self.max_spectra))
        self.set_max_spectra_button = ttk.Button(
            self.wasatch_parameters_frame,
            text="Set",
            command=self.set_max_spectra,
            width=4,
        )
        self.set_max_spectra_button.grid(row=1, column=8, padx=PADX, pady=(PADY, PADY))

        for c in range(9):
            if c in (0, 3, 6):
                minsize = 120
            elif c in (2, 5, 8):
                minsize = 50
            else:
                minsize = 0
            self.wasatch_parameters_frame.grid_columnconfigure(c, weight=1, minsize=minsize)

        # Measuring frame
        self.wasatch_measure_frame = ttk.LabelFrame(self.controls_frame, text="Measuring", **FRAME_KWARGS)
        self.wasatch_measure_frame.pack(fill='x', padx=PADX, pady=1)

        # sample counts in a single row
        self.wasatch_samples_countX_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countX_entry.grid(row=0, column=1, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.wasatch_samples_countX_entry.insert(tk.END, '10')

        self.wasatch_samples_countY_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countY_entry.grid(row=0, column=3, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.wasatch_samples_countY_entry.insert(tk.END, '10')

        self.wasatch_samples_countZ_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countZ_entry.grid(row=0, column=5, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.wasatch_samples_countZ_entry.insert(tk.END, '1')

        ttk.Label(self.wasatch_measure_frame, text="X:").grid(row=0, column=0, padx=PADX, pady=(PADY, 0), sticky="e")
        ttk.Label(self.wasatch_measure_frame, text="Y:").grid(row=0, column=2, padx=PADX, pady=(PADY, 0), sticky="e")
        ttk.Label(self.wasatch_measure_frame, text="Z:").grid(row=0, column=4, padx=PADX, pady=(PADY, 0), sticky="e")

        for c in range(6):
            self.wasatch_measure_frame.grid_columnconfigure(c, weight=1)
        for entry in (
            self.wasatch_samples_countX_entry,
            self.wasatch_samples_countY_entry,
            self.wasatch_samples_countZ_entry,
        ):
            entry.bind("<KeyRelease>", lambda e: self.calculate_predicted_points())

        # dark and light reference buttons in same row
        self.wasatch_dark_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Dark",
            command=self.run_dark,
            state=tk.DISABLED,
        )
        self.wasatch_dark_button.grid(row=1, column=0, columnspan=3, padx=PADX, pady=(PADY, 0), sticky="ew")

        self.wasatch_light_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Light",
            command=self.run_light,
            state=tk.DISABLED,
        )
        self.wasatch_light_button.grid(row=1, column=3, columnspan=3, padx=PADX, pady=(PADY, 0), sticky="ew")

        # Progress bar with percentage overlay
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            self.wasatch_measure_frame,
            orient="horizontal",
            mode="determinate",
            variable=self.progress_var,
            maximum=100,
        )
        self.progress_bar.grid(row=2, column=0, columnspan=6, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.progress_label = ttk.Label(self.progress_bar, text="0 %", anchor="center")
        self.progress_label.place(relx=0.5, rely=0.5, anchor="center")

        # label and description on one row
        self.run_once_label = ttk.Label(self.wasatch_measure_frame, text="Label:")
        self.run_once_label.grid(row=3, column=0, padx=PADX, pady=(PADY, 0), sticky="w")

        self.run_once_description = ttk.Entry(self.wasatch_measure_frame, width=30)
        self.run_once_description.grid(row=3, column=1, columnspan=5, padx=PADX, pady=(PADY, 0), sticky="ew")

        # measurement control buttons
        self.run_once_button = ttk.Button(self.wasatch_measure_frame, text="Run once", command=self.start_measurement_once)
        self.run_once_button.grid(row=4, column=0, columnspan=2, padx=PADX, pady=(PADY, 0), sticky="ew")

        self.run_area_button = ttk.Button(self.wasatch_measure_frame, text="Run area", command=self.start_measurement)
        self.run_area_button.grid(row=4, column=2, columnspan=2, padx=PADX, pady=(PADY, 0), sticky="ew")

        self.wasatch_stop_button = ttk.Button(self.wasatch_measure_frame, text="Stop", command=self.stop_measurement)
        self.wasatch_stop_button.grid(row=4, column=4, columnspan=2, padx=PADX, pady=(PADY, 0), sticky="ew")

        self.pause_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Pause for reference",
            command=self.pause_for_reference
        )
        self.pause_button.grid(row=5, column=0, columnspan=3, padx=PADX, pady=(PADY, 0), sticky="ew")
        self.continue_button = ttk.Button(
            self.wasatch_measure_frame,
            text="Continue",
            command=self.continue_measurement,
            state=tk.DISABLED
        )
        self.continue_button.grid(row=5, column=3, columnspan=2, padx=PADX, pady=(PADY, 0), sticky="ew")

        # Dark mode toggle inside measuring frame
        self.dark_button = ttk.Button(self.wasatch_measure_frame, text="Dark Mode", command=self.toggle_dark_mode)
        self.dark_button.grid(row=5, column=5, padx=PADX, pady=(PADY, 0), sticky="ew")

        self.estimated_time_var = tk.StringVar(value="Estimated time: n/a")
        self.estimated_time_label = ttk.Label(
            self.wasatch_measure_frame, textvariable=self.estimated_time_var
        )
        self.estimated_time_label.grid(row=6, column=0, columnspan=6, padx=PADX, pady=(PADY, PADY), sticky="ew")

        self.time_calc_progress = ttk.Progressbar(
            self.wasatch_measure_frame, orient="horizontal", length=300, mode="determinate"
        )
        self.time_calc_progress.grid(row=7, column=0, columnspan=6, padx=PADX, pady=(0, PADY), sticky="ew")

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

        # 3D head position (lewy wykres)
        self.map_frame = ttk.LabelFrame(self.plots_frame, text="Head position 3D", **FRAME_KWARGS)
        self.map_frame.grid(row=0, column=0, sticky='nsew', padx=(0, PADX))
        self.map_fig = Figure(figsize=(4, 3), dpi=100)

        self.map_ax = self.map_fig.add_subplot(111, projection='3d')
        try:
            from matplotlib import ticker as _ticker
            self.map_ax.yaxis.set_major_locator(_ticker.MaxNLocator(prune='lower'))
            self.map_ax.zaxis.set_major_locator(_ticker.MaxNLocator(prune='lower'))
        except Exception:
            pass
        try:
            self.map_ax.set_proj_type('ortho')
        except Exception:
            pass
        self.map_canvas = FigureCanvasTkAgg(self.map_fig, master=self.map_frame)
        self.map_canvas.draw()
        self.map_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.position_label = ttk.Label(self.map_frame, text="X:0 Y:0 Z:0")
        self.position_label.pack()
        ttk.Button(self.map_frame, text="Mark position", command=self.mark_current_position).pack(pady=(0, PADY))

        # Scan points (prawy wykres)
        self.points_frame = ttk.LabelFrame(self.plots_frame, text="Scan points", **FRAME_KWARGS)
        self.points_frame.grid(row=0, column=1, sticky='nsew')

        self.wasatch.init_points_plot(self.points_frame)
        zoom_frame = ttk.Frame(self.points_frame)
        zoom_frame.pack()
        ttk.Button(zoom_frame, text="🔍+", command=lambda: self.zoom_axes(self.wasatch.points_ax, self.wasatch.points_canvas, 0.8, True)).pack(side="left")
        ttk.Button(zoom_frame, text="🔍-", command=lambda: self.zoom_axes(self.wasatch.points_ax, self.wasatch.points_canvas, 1.25, True)).pack(side="left")

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

    def toggle_dark_mode(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.wasatch.set_dark_mode(self.dark_mode)
        self.dark_button.config(text="Light Mode" if self.dark_mode else "Dark Mode")

    def apply_theme(self):
        if self.dark_mode:
            bg = APP_BG
            frame_bg = CARD_BG
            entry_bg = INPUT_BG
            fg = TEXT_PRIMARY
            self.style.configure('Horizontal.TProgressbar', troughcolor=PROGRESS_BG,
                                 background=PROGRESS_FILL)
        else:
            bg = LIGHT_BG
            frame_bg = LIGHT_BG
            entry_bg = 'white'
            fg = LIGHT_FG
            self.style.configure('Horizontal.TProgressbar', troughcolor='#f0f0f0',
                                 background='#4a90e2')

        self.root.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.style.configure('TFrame', background=bg)
        self.style.configure('TLabelframe', background=frame_bg, foreground=fg)
        self.style.configure('TLabelframe.Label', background=frame_bg, foreground=fg)
        self.style.configure('TLabel', background=frame_bg, foreground=fg)
        self.style.configure('TButton', background=entry_bg, foreground=fg, bordercolor=BORDER_COLOR)
        self.style.map('TButton', background=[('active', ACCENT)])
        self.style.configure('TEntry', fieldbackground=entry_bg, foreground=fg)
        self.style.configure('TCombobox', fieldbackground=entry_bg, foreground=fg)
        self.marked_list.config(bg=entry_bg, fg=fg, selectbackground=ACCENT, selectforeground=bg)
        self.log_text.config(bg=entry_bg, fg=fg, insertbackground=fg)
        # apply colors to 3D head position plot
        if hasattr(self, 'map_ax'):
            self.map_fig.set_facecolor(bg)
            self.map_ax.set_facecolor(frame_bg)
            for axis in [self.map_ax.xaxis, self.map_ax.yaxis, self.map_ax.zaxis]:
                axis.label.set_color(fg)
                axis.set_tick_params(colors=fg)
            for axis in [self.map_ax.xaxis, self.map_ax.yaxis, self.map_ax.zaxis]:

                axis.line.set_color(fg)

    def update_progress(self, progress):
        def _do():
            self.progress_var.set(progress)
            self.progress_label['text'] = f"{progress} %"
        self.root.after(0, _do)

    def update_time_calc_progress(self, progress):
        def _do():
            self.time_calc_progress["value"] = progress
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
            xr = ax.get_xlim3d()[1] - ax.get_xlim3d()[0]
            yr = ax.get_ylim3d()[1] - ax.get_ylim3d()[0]
            zr = ax.get_zlim3d()[1] - ax.get_zlim3d()[0]
            ax.set_box_aspect([xr, yr, zr])
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
        if ax is self.wasatch.points_ax:
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

        defined = {k: v for k, v in self.user_positions.items() if v}
        if len(defined) < 2:
            self.wasatch.scan_points = self.user_positions
            self.wasatch.points_zoomed = False
            self.wasatch.update_points_plot()
            self.estimated_time_var.set("Estimated time: n/a")
            return

        xs = [p['X'] for p in defined.values()]
        ys = [p['Y'] for p in defined.values()]
        zs = [p['Z'] for p in defined.values()]

        if '1' in defined:
            x1 = defined['1']['X']
            y1 = defined['1']['Y']
            z1 = defined['1']['Z']
        else:
            x1 = min(xs)
            y1 = min(ys)
            z1 = min(zs)

        x2 = max(xs) if x1 == min(xs) else min(xs)
        y2 = max(ys) if y1 == min(ys) else min(ys)
        z2 = max(zs) if z1 == min(zs) else min(zs)

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
        def worker():
            try:
                self.update_time_calc_progress(0)
                self.wasatch.points_zoomed = False
                self.wasatch.set_scan_bounds(
                    x1, x2,
                    y1, y2,
                    z1, z2,
                    self.user_positions,
                    count_x,
                    count_y,
                    count_z,
                    progress_cb=self.update_time_calc_progress,
                    log_cb=self.log,
                )
                self.log("Points ready")
                self.update_time_estimate()
            except Exception:
                self.wasatch.scan_points = self.user_positions

        threading.Thread(target=worker, daemon=True).start()

    def update_time_estimate(self):
        self.update_time_calc_progress(0)
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
            xs = [p['X'] for p in defined]
            ys = [p['Y'] for p in defined]
            zs = [p['Z'] for p in defined]
            if self.user_positions.get('1'):
                start_x = self.user_positions['1']['X']
                start_y = self.user_positions['1']['Y']
                start_z = self.user_positions['1']['Z']
            else:
                start_x = min(xs)
                start_y = min(ys)
                start_z = min(zs)
            end_x = max(xs) if start_x == min(xs) else min(xs)
            end_y = max(ys) if start_y == min(ys) else min(ys)
            end_z = max(zs) if start_z == min(zs) else min(zs)
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
        self.update_time_calc_progress(100)

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
        elif direction == '\u2190':  # Left (X-)
            self.current_position['X'] -= step
            command = f'G1 X-{step} F{speed}'
        elif direction == '\u2192':  # Right (X+)
            self.current_position['X'] += step
            command = f'G1 X{step} F{speed}'
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
            self.current_position['X'] = x
            self.current_position['Y'] = y
            self.current_position['Z'] = z
            self.position_label.config(text=f"X:{x:.1f} Y:{y:.1f} Z:{z:.1f}")
            self.wasatch.position = (x, y, z)
            self.update_volume_display()
        self.wasatch.update_points_plot()

        self.root.after(0, _do)

    def auto_update_map(self):
        self.update_map_position(
            self.current_position['X'],
            self.current_position['Y'],
            self.current_position['Z']
        )
        self.root.after(200, self.auto_update_map)

    def mark_current_position(self):
        idx = len(self.marked_positions) + 1
        pos = {
            'X': self.current_position['X'],
            'Y': self.current_position['Y'],
            'Z': self.current_position['Z'],
            'index': idx,
        }
        self.marked_positions.append(pos)
        self.marked_list.insert(tk.END, f"{idx}: X{pos['X']:.2f} Y{pos['Y']:.2f} Z{pos['Z']:.2f}")
        self.update_volume_display()

    def delete_marked_point(self):
        sel = self.marked_list.curselection()
        if not sel:
            return
        index = sel[0]
        del self.marked_positions[index]
        self.marked_list.delete(0, tk.END)
        for i, pos in enumerate(self.marked_positions, start=1):
            pos['index'] = i
            self.marked_list.insert(tk.END, f"{i}: X{pos['X']:.2f} Y{pos['Y']:.2f} Z{pos['Z']:.2f}")
        self.update_volume_display()

    def update_volume_display(self):
        import itertools

        self.map_ax.cla()
        try:
            self.map_ax.set_proj_type('ortho')
        except Exception:
            pass

        xs = []
        ys = []
        zs = []

        # current head position
        x = self.current_position['X']
        y = self.current_position['Y']
        z = self.current_position['Z']
        self.map_ax.scatter([x], [y], [z], c='red', s=80, alpha=0.6)
        xs.append(x)
        ys.append(y)
        zs.append(z)

        # draw reference points if any
        colors = ['blue', 'green', 'magenta', 'orange', 'cyan']
        for idx, key in enumerate(['1','2','3','4','5']):
            pt = self.user_positions.get(key)
            if pt:
                self.map_ax.scatter([pt['X']], [pt['Y']], [pt['Z']], color=colors[idx])
                self.map_ax.text(pt['X'], pt['Y'], pt['Z'], f"{key}")
                xs.append(pt['X'])
                ys.append(pt['Y'])
                zs.append(pt['Z'])

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
                self.map_ax.plot(
                    [corners[e[0]][0], corners[e[1]][0]],
                    [corners[e[0]][1], corners[e[1]][1]],
                    [corners[e[0]][2], corners[e[1]][2]],
                    color='black'
                )

        # draw marked positions
        for pos in self.marked_positions:
            self.map_ax.scatter([pos['X']], [pos['Y']], [pos['Z']], color='red', marker='x')
            self.map_ax.text(pos['X'], pos['Y'], pos['Z'], str(pos['index']), color='red')
            xs.append(pos['X'])
            ys.append(pos['Y'])
            zs.append(pos['Z'])

        if xs and ys and zs:
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            zmin, zmax = min(zs), max(zs)

            # Expand collapsed axes to avoid singular transformations
            if xmin == xmax:
                xmin -= 0.5
                xmax += 0.5
            if ymin == ymax:
                ymin -= 0.5
                ymax += 0.5
            if zmin == zmax:
                zmin -= 0.5
                zmax += 0.5

            self.map_ax.set_xlim(xmin, xmax)
            self.map_ax.set_ylim(ymin, ymax)
            self.map_ax.set_zlim(zmin, zmax)

            # Hide ticks when the corresponding axis collapses
            self.map_ax.set_xticks([] if xmin == xmax else None)
            self.map_ax.set_yticks([] if ymin == ymax else None)
            self.map_ax.set_zticks([] if zmin == zmax else None)

        self.map_ax.set_box_aspect([1, 1, 1])
        self.map_ax.set_xlabel('X')
        self.map_ax.set_ylabel('Y')
        self.map_ax.set_zlabel('Z')
        self.map_canvas.draw()
        self.wasatch.update_marked_points(self.marked_positions)


    def set_position(self, position_number):
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
