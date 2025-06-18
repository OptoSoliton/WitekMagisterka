import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import time
import threading
import numpy as np

class MyGUI:
    def __init__(self, root, serial_connection, wasatch):
        self.root = root
        self.serial = serial_connection
        self.wasatch = wasatch
        self.root.title("CNC & Wasatch Controller")

        # Actual X and Y position
        self.current_position = {'X': 0, 'Y': 0}
        self.user_positions = {'2': None, '3': None, '4': None}

        self.integration_time = 10
        self.scans_to_average = 1
        self.boxcar_half_width = 0
        self.delay_ms = 1000
        self.max_spectra = 0

        self.samples_count_x = 1
        self.samples_count_y = 1

        self.output_file = None

        self.setup_ui()
        self.running = False
        self.measure_thread = threading.Thread()

    def setup_ui(self):

        # Left and right part of window
        self.left_frame = ttk.PanedWindow(self.root)
        self.left_frame.grid(row=0, column=0, sticky='n')

        self.right_frame = ttk.PanedWindow(self.root)
        self.right_frame.grid(row=0, column=1, sticky='n')

        # Serial connection frame
        self.serial_connection_frame = ttk.LabelFrame(self.left_frame, text="CNC serial connection")
        self.serial_connection_frame.grid(row=0, column=0, padx=10, pady=5, sticky="ew")

        self.serial_port_label = ttk.Label(self.serial_connection_frame, text="Port COM:")
        self.serial_port_label.grid(row=0, column=0, padx=10, pady=10)

        self.serial_ports = self.serial.list_serial_ports()
        self.serial_port_combobox = ttk.Combobox(self.serial_connection_frame, values=self.serial_ports)
        self.serial_port_combobox.grid(row=0, column=1, padx=10, pady=10)

        self.connect_button = ttk.Button(self.serial_connection_frame, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, padx=10, pady=10)

        # Control frame for movement
        self.control_frame = ttk.LabelFrame(self.left_frame, text="CNC control")
        self.control_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")

        # Movement buttons
        button = ttk.Button(self.control_frame, text='\u2190', 
        command=lambda d='\u2190': self.move(d)) # Left
        button.grid(row=1, column=0, padx=10, pady=10)
    
        button = ttk.Button(self.control_frame, text='\u2191', command=lambda d='\u2191': self.move(d)) # Up
        button.grid(row=0, column=1, padx=10, pady=10)
    
        button = ttk.Button(self.control_frame, text='\u2192', command=lambda d='\u2192': self.move(d)) # Right
        button.grid(row=1, column=2, padx=10, pady=10)
    
        button = ttk.Button(self.control_frame, text='\u2193', command=lambda d='\u2193': self.move(d)) # Down
        button.grid(row=2, column=1,  padx=10, pady=10)

        # Z-axis control
        self.up_button = ttk.Button(self.control_frame, text="Up", command=lambda: self.move('Up'))
        self.up_button.grid(row=0, column=3, padx=10, pady=10)

        self.down_button = ttk.Button(self.control_frame, text="Down", command=lambda: self.move('Down'))
        self.down_button.grid(row=2, column=3, padx=10, pady=10)

        # Adding "0,0" Button to reset position
        self.zero_button = ttk.Button(self.control_frame, text="Set 0.0", command=lambda: self.set_position_zero())
        self.zero_button.grid(row=1, column=1, padx=10, pady=10)

        # Step and Speed frame
        self.step_speed_frame = ttk.LabelFrame(self.left_frame, text="CNC control parameters")
        self.step_speed_frame.grid(row=2, column=0, padx=10, pady=5, sticky="ew")

        self.step_label = ttk.Label(self.step_speed_frame, text="Step:")
        self.step_label.grid(row=0, column=0, padx=10, pady=5)
        self.step_entry = ttk.Entry(self.step_speed_frame)
        self.step_entry.grid(row=0, column=1, padx=10, pady=5)
        self.step_entry.insert(tk.END, '10')  # Default value

        self.speed_label = ttk.Label(self.step_speed_frame, text="Speed:")
        self.speed_label.grid(row=0, column=2, padx=10, pady=5)
        self.speed_entry = ttk.Entry(self.step_speed_frame)
        self.speed_entry.grid(row=0, column=3, padx=10, pady=5)
        self.speed_entry.insert(tk.END, '1000')  # Default value

        # Position setting and test movement frame
        self.position_frame = ttk.LabelFrame(self.left_frame, text="Position configuration")
        self.position_frame.grid(row=3, column=0, padx=10, pady=5, sticky="ew")

        # Position buttons
        self.set_button_1 = ttk.Button(self.position_frame, text="Set 1", command=lambda: self.set_position(1))
        self.set_button_1.grid(row=0, column=0, padx=10, pady=5)

        self.set_button_2 = ttk.Button(self.position_frame, text="Set 2", command=lambda: self.set_position(2))
        self.set_button_2.grid(row=0, column=1, padx=10, pady=5)

        self.set_button_3 = ttk.Button(self.position_frame, text="Set 3", command=lambda: self.set_position(3))
        self.set_button_3.grid(row=1, column=1, padx=10, pady=5)

        self.set_button_4 = ttk.Button(self.position_frame, text="Set 4", command=lambda: self.set_position(4))
        self.set_button_4.grid(row=1, column=0, padx=10, pady=5)

        self.test_button = ttk.Button(self.position_frame, text="Test", command=self.test_positions)
        self.test_button.grid(row=1, column=2, padx=50, pady=5)

        # Adding "Init" Button to send initialization commands
        self.init_button = ttk.Button(self.position_frame, text="Goto 0.0", command=self.move_to_zero)
        self.init_button.grid(row=0, column=2,  padx=50, pady=10)
        
        # Manual command frame
        self.manual_command_frame = ttk.LabelFrame(self.left_frame, text="Manual command")
        self.manual_command_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")

        self.manual_command_label = ttk.Label(self.manual_command_frame, text="Command:")
        self.manual_command_label.grid(row=0, column=0, padx=10, pady=10)

        self.manual_command_entry = ttk.Entry(self.manual_command_frame, width=30)
        self.manual_command_entry.grid(row=0, column=1,  padx=10, pady=10)

        self.manual_command_button = ttk.Button(self.manual_command_frame, text="Send", command=self.send_manual_command)
        self.manual_command_button.grid(row=0, column=2, padx=10, pady=10)

        # Log frame
        self.log_frame = ttk.LabelFrame(self.left_frame, text="Log")
        self.log_frame.grid(row=5, column=0, padx=10, pady=5, sticky="ew")

        self.log_text = scrolledtext.ScrolledText(self.log_frame, wrap=tk.WORD, height=8, width=50)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Spectrometer connection frame
        self.wasatch_connection_frame = ttk.LabelFrame(self.right_frame, text="Wasatch connection")
        self.wasatch_connection_frame.grid(row=0, column=1, padx=10, pady=5, sticky="nsew")

        self.wasatch_connect_button = ttk.Button(self.wasatch_connection_frame, text = "Connect spectrometer", command=self.wasatch.connect)
        self.wasatch_connect_button.grid(row=0, column=0, padx=50, pady=5)

        # File path selection for saving spectra
        self.file_path_frame = ttk.LabelFrame(self.right_frame, text="File path for spectra")
        self.file_path_frame.grid(row=1, column=1, padx=10, pady=5, sticky="ew")

        self.file_path_label = ttk.Label(self.file_path_frame, text="File path:")
        self.file_path_label.grid(row=0, column=0, padx=10, pady=10)

        self.file_path_entry = ttk.Entry(self.file_path_frame, width=30)
        self.file_path_entry.grid(row=0, column=1, padx=10, pady=10)

        self.file_path_button = ttk.Button(self.file_path_frame, text="Browse", command=self.browse_file_path)
        self.file_path_button.grid(row=0, column=2, padx=10, pady=10)

        # Wasatch parameters
        self.wasatch_parameters_frame = ttk.LabelFrame(self.right_frame, text="Parameters")
        self.wasatch_parameters_frame.grid(row=2, column=1, padx=10, pady=5, sticky="ew")

        # Integration time control
        self.integration_time_label = ttk.Label(self.wasatch_parameters_frame, text="Integration Time (ms):")
        self.integration_time_label.grid(row=2, column=0, padx=10, pady=5)
        self.integration_time_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.integration_time_entry.grid(row=2, column=1, padx=10, pady=5)
        self.integration_time_entry.insert(tk.END, str(self.integration_time))  # Default value

        self.set_integration_time_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_integration_time)
        self.set_integration_time_button.grid(row=2, column=2, padx=10, pady=5)

        # Scans to average control
        self.scans_to_average_label = ttk.Label(self.wasatch_parameters_frame, text="Scans to Average:")
        self.scans_to_average_label.grid(row=3, column=0, padx=10, pady=5)
        self.scans_to_average_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.scans_to_average_entry.grid(row=3, column=1, padx=10, pady=5)
        self.scans_to_average_entry.insert(tk.END, str(self.scans_to_average))  # Default value

        self.set_scans_to_average_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_scans_to_average)
        self.set_scans_to_average_button.grid(row=3, column=2, padx=10, pady=5)

        # Boxcar half width control
        self.boxcar_half_width_label = ttk.Label(self.wasatch_parameters_frame, text="Boxcar Half Width:")
        self.boxcar_half_width_label.grid(row=4, column=0, padx=10, pady=5)
        self.boxcar_half_width_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.boxcar_half_width_entry.grid(row=4, column=1, padx=10, pady=5)
        self.boxcar_half_width_entry.insert(tk.END, str(self.boxcar_half_width))  # Default value

        self.set_boxcar_half_width_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_boxcar_half_width)
        self.set_boxcar_half_width_button.grid(row=4, column=2, padx=10, pady=5)

        # Delay ms control
        self.delay_ms_label = ttk.Label(self.wasatch_parameters_frame, text="Delay (ms):")
        self.delay_ms_label.grid(row=5, column=0, padx=10, pady=5)
        self.delay_ms_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.delay_ms_entry.grid(row=5, column=1, padx=10, pady=5)
        self.delay_ms_entry.insert(tk.END, str(self.delay_ms))  # Default value

        self.set_delay_ms_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_delay_ms)
        self.set_delay_ms_button.grid(row=5, column=2, padx=10, pady=5)

        # Max spectra control
        self.max_spectra_label = ttk.Label(self.wasatch_parameters_frame, text="Max Spectra:")
        self.max_spectra_label.grid(row=6, column=0, padx=10, pady=5)
        self.max_spectra_entry = ttk.Entry(self.wasatch_parameters_frame)
        self.max_spectra_entry.grid(row=6, column=1, padx=10, pady=5)
        self.max_spectra_entry.insert(tk.END, str(self.max_spectra))  # Default value

        self.set_max_spectra_button = ttk.Button(self.wasatch_parameters_frame, text="Set", command=self.set_max_spectra)
        self.set_max_spectra_button.grid(row=6, column=2, padx=10, pady=5)

        # Measuring frame
        self.wasatch_measure_frame = ttk.LabelFrame(self.right_frame, text="Measuring")
        self.wasatch_measure_frame.grid(row=3, column=1, padx=10, pady=5, sticky="nsew")

        self.wasatch_samples_countX_label = ttk.Label(self.wasatch_measure_frame, text="Sample count X axis")
        self.wasatch_samples_countX_label.grid(row=0, column=1, padx=50, pady=5)
        self.wasatch_samples_countX_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countX_entry.grid(row=0, column=2, padx=10, pady=5)
        self.wasatch_samples_countX_entry.insert(tk.END, '10')  # Default value

        self.wasatch_samples_countY_label = ttk.Label(self.wasatch_measure_frame, text="Sample count Y axis")
        self.wasatch_samples_countY_label.grid(row=1, column=1, padx=50, pady=5)
        self.wasatch_samples_countY_entry = ttk.Entry(self.wasatch_measure_frame)
        self.wasatch_samples_countY_entry.grid(row=1, column=2, padx=10, pady=5)
        self.wasatch_samples_countY_entry.insert(tk.END, '10')  # Default value

        self.wasatch_dark_button = ttk.Button(self.wasatch_measure_frame, text = "Dark", command=self.run_dark)
        self.wasatch_dark_button.grid(row=2, column=0, columnspan=2, padx=5, pady=5)

        self.wasatch_light_button = ttk.Button(self.wasatch_measure_frame, text = "Light", command=self.run_light)
        self.wasatch_light_button.grid(row=2, column=1, columnspan=2, padx=5, pady=5)

        self.toggle_plot_button = ttk.Button(self.wasatch_measure_frame, text="Show plot", command=self.toggle_plot)
        self.toggle_plot_button.grid(row=2, column=2, columnspan=2, padx=5, pady=5)

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.wasatch_measure_frame, orient="horizontal", length=300,  mode="determinate")
        self.progress_bar.grid(row=3, column=0, columnspan=4, padx=10, pady=5)

        # Progress bar label
        self.progress_label = ttk.Label(self.wasatch_measure_frame, text="0 %", anchor="center")
        self.progress_label.grid(row=3, column=3, padx=10, pady=25)

        self.run_once_description = ttk.Label(self.wasatch_measure_frame, text="Label:")
        self.run_once_description.grid(row=4, column=0, columnspan=1, padx=10, pady=10)

        self.run_once_description = ttk.Entry(self.wasatch_measure_frame, width=30)
        self.run_once_description.grid(row=4, column=1, columnspan=2, padx=10, pady=10)

        self.wasatch_run_button = ttk.Button(self.wasatch_measure_frame, text = "Run once", command=self.start_measurement_once)
        self.wasatch_run_button.grid(row=5, column=0, columnspan=2, padx=5, pady=5)
        
        self.wasatch_run_button = ttk.Button(self.wasatch_measure_frame, text = "Run area", command=self.start_measurement)
        self.wasatch_run_button.grid(row=5, column=1, columnspan=3, padx=5, pady=5)

        self.wasatch_stop_button = ttk.Button(self.wasatch_measure_frame, text = "Stop", command=self.stop_measurement)
        self.wasatch_stop_button.grid(row=5, column=2, columnspan=2, padx=5, pady=5)

    def toggle_plot(self):
        if self.toggle_plot_button["text"] == "Show plot":
            self.toggle_plot_button.config(text="Hide plot")
        else:
            self.toggle_plot_button.config(text="Show plot")
        self.wasatch.toggle_plot()

    def update_progress(self, progress):
        self.progress_bar["value"] = progress
        self.progress_label['text'] = str(progress) + ' %'
        self.root.update_idletasks()

    def set_integration_time(self):
        integration_time = int(self.integration_time_entry.get())
        self.wasatch.set_integration_time(integration_time)

    def set_scans_to_average(self):
        scans_to_average = int(self.scans_to_average_entry.get())
        self.wasatch.set_scans_to_average(scans_to_average)

    def set_boxcar_half_width(self):
        boxcar_half_width = int(self.boxcar_half_width_entry.get())
        self.wasatch.set_boxcar_half_width(boxcar_half_width)

    def set_delay_ms(self):
        delay_ms = int(self.delay_ms_entry.get())
        self.wasatch.set_delay_ms(delay_ms)

    def set_max_spectra(self):
        max_spectra = int(self.max_spectra_entry.get())
        self.wasatch.set_max_spectra(max_spectra)

    def browse_file_path(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if file_path:
            self.file_path_entry.delete(0, tk.END)
            self.file_path_entry.insert(0, file_path)
            self.wasatch.set_output_file_path(file_path)

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
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def move(self, direction):
        step = float(self.get_step())
        speed = self.get_speed()

        if direction == '\u2191':  # Front (Y+)
            self.current_position['Y'] -= step
            command = f'G1 Y{step} F{speed}'
        elif direction =='\u2193':  # Rear (Y-)
            self.current_position['Y'] += step
            command = f'G1 Y-{step} F{speed}'
        elif direction == '\u2190':  # Left (X-)
            self.current_position['X'] -= step
            command = f'G1 X{step} F{speed}'
        elif direction == '\u2192':  # Right (X+)
            self.current_position['X'] += step
            command = f'G1 X-{step} F{speed}'
        elif direction == 'Up':  # Up (Z+)
            command = f'G1 Z{step} F{speed}'
        elif direction == 'Down':  # Down (Z-)
            command = f'G1 Z-{step} F{speed}'
        else:
            command = ''

        if command:
            log_message = self.serial.send_gcode('G91')
            log_message = self.serial.send_gcode(command)
            self.log(log_message)
            log_message = self.serial.send_gcode('G90')

    def get_step(self):
        return self.step_entry.get()

    def get_speed(self):
        return self.speed_entry.get()


    def send_manual_command(self):
        command = self.manual_command_entry.get()
        log_message = self.serial.send_gcode(command)
        self.log(log_message)

    def set_position(self, position_number):
        self.user_positions[str(position_number)] = self.current_position.copy()
        self.log(f"Position {position_number} set to X:{self.current_position['X']}, Y:{self.current_position['Y']}.")

    def test_positions(self):
        # Move to the initial position 0,0 first
        self.serial.send_gcode('G90')

        for pos_number in range(1, 5):
            position = self.user_positions[str(pos_number)]
            if position is not None:
                command = f"G1 X-{position['X']} Y{-position['Y']} F1000"
                self.serial.send_gcode(command)
                self.log(f"Moving to position {pos_number}: X={position['X']}, Y={position['Y']}")
                time.sleep(int(self.get_step())/10)
        # Move back to position 1
        position_1 = self.user_positions['1']
        if position_1 is not None:
            command = f"G1 X-{position_1['X']} Y{position_1['Y']} F1000"
            self.serial.send_gcode(command)
            self.log(f"Returning to position 1: X={position_1['X']}, Y={position_1['Y']}")
            time.sleep(1)
        else:
            self.log("Position 1 is not selected")

    def set_position_zero(self):
        self.current_position = {'X': 0, 'Y': 0}
        self.serial.send_gcode('G92 X0 Y0 Z0')
        self.log("Current position set as 0,0.")

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

    def start_measurement(self):
        self.stop_measurement()

        if not self.running:
            self.running = True
            # New thread for measuring
            if not self.measure_thread or not self.measure_thread.is_alive():
                self.measure_thread = threading.Thread(target=self.measure_and_move)
                self.measure_thread.start()  # Run thread

    def start_measurement_once(self):
        run_once_string = self.run_once_description.get()
        if run_once_string =='':
            run_once_string='single measure'
        self.wasatch.init_file()
        self.log(run_once_string)
        finished = self.wasatch.run(run_once_string)
        self.wasatch.close_file()


        if finished == False:
            self.running = False
            self.log("Stopped. Measure from wasatch.py return False.")
            return

    def stop_measurement(self):
        self.running = False

    def measure_and_move(self):
        self.update_progress(0)

        self.samples_count_x = int(self.wasatch_samples_countX_entry.get())
        self.samples_count_y = int(self.wasatch_samples_countY_entry.get())

        step_x = (self.user_positions['2']['X'] - self.user_positions['1']['X']) / (self.samples_count_x - 1)
        step_y = (self.user_positions['4']['Y'] - self.user_positions['1']['Y']) / (self.samples_count_y - 1)

        # Turn cnc into start point (0,0)
        self.serial.send_gcode('G90')
        move_command = f'G1 X-{self.user_positions["1"]["X"]} Y{self.user_positions["1"]["Y"]} F{self.get_speed()}'
        self.serial.send_gcode(move_command)
        self.log("Moving to start position")
        self.waitForCNC()

        current_measure = 0
        measure_count = self.samples_count_x * self.samples_count_y

        self.wasatch.init_file()

        while self.running:
            # Move cnc
            for i in range(self.samples_count_x):
                new_x = self.user_positions['1']['X'] + i * step_x
                isChangedX = True
                for j in range(self.samples_count_y):
                    if not self.running:  
                        break
                    # New position Y
                    new_y = self.user_positions['1']['Y'] + j * step_y
                    # Move cnc to new position
                    move_command = f'G1 X-{new_x} Y-{new_y} F{self.get_speed()}'
                    self.serial.send_gcode(move_command)
                    self.log(f"Moving to position X: {new_x}, Y: {new_y}")
                    self.waitForCNC()
                    max_step = max(step_x,step_y)
                    self.measureDelayFromSteps(max_step)
                    if(isChangedX):
                        self.measureDelayFromSteps(step_x*self.samples_count_x)
                        isChangedX=False
                    current_measure += 1
                    self.log(f"Measure {current_measure} out of {measure_count}.")
                    finished = self.wasatch.run("%2.4f, %2.4f" % (new_x, new_y))
                    progress = int((current_measure / measure_count) * 100)

                    self.update_progress(progress)
                    if not finished:
                        self.running = False
                        self.log("Stopped. Measure from wasatch.py returned False.")
                        break

                if not self.running:  
                    self.log("Stopped.")
                    break

            self.running = False

    def waitForCNC(self):
        while not self.serial.wait_for_ending_move():
            pass

    def measureDelayFromSteps(self, step):
        wait_time_ms = self.interpolate_time(step) + 100
        time.sleep(wait_time_ms/1000)

    def interpolate_time(self, step):
        steps = np.array([1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 100, 120, 150])
        times = np.array([500, 1150, 1560, 2000, 2700, 3300, 3800, 4400, 4900, 5500, 6600, 7800, 9700])
        if step <= steps[0]:
            return times[0]
        else:
            return np.interp(step, steps, times)

    def run_dark(self):
        self.wasatch.init_file()
        self.log(f"Dark measure")
        finished = self.wasatch.run("dark")
        self.wasatch.close_file()

        if finished == False:
            self.running = False
            self.log("Stopped. Measure from wasatch.py return False.")
            return
        
    def run_light(self):
        self.wasatch.init_file()
        self.log(f"Light measure")
        finished = self.wasatch.run("light")
        self.wasatch.close_file()

        if finished == False:
            self.running = False
            self.log("Stopped. Measure from wasatch.py return False.")
            return