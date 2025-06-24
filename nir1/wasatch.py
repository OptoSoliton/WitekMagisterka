import os
import re
import sys
import time
import numpy
import signal
import psutil
import logging
import datetime
import argparse
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import wasatch
from wasatch import utils
from wasatch import applog
from wasatch.WasatchBus           import WasatchBus
from wasatch.OceanDevice          import OceanDevice
from wasatch.WasatchDevice        import WasatchDevice
from wasatch.WasatchDeviceWrapper import WasatchDeviceWrapper
from wasatch.RealUSBDevice        import RealUSBDevice
import logging

log = logging.getLogger(__name__)

class Wasatch:
    def __init__(self, root, argv=None):
        self.bus     = None
        self.device  = None
        self.logger  = None
        self.outfile = None
        self.type = "default"
        self.light_spectrum = None
        self.args = self.parse_args(argv)
        self.logger = applog.MainLogger(self.args.log_level)
        print("Wasatch.PY version %s", wasatch.__version__)
        self.root = root

        # Create tkinter window for plot
        self.graph_window = tk.Toplevel(self.root)
        self.graph_window.title("Measurement plot")
        self.graph_window.protocol("WM_DELETE_WINDOW", self.graph_window.withdraw)
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_window)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        self.graph_window.withdraw()

        # Window for measured points
        self.points_window = tk.Toplevel(self.root)
        self.points_window.title("Measured points")
        self.points_window.protocol("WM_DELETE_WINDOW", self.points_window.withdraw)
        self.points_fig = Figure(figsize=(4, 4), dpi=100)
        self.points_ax = self.points_fig.add_subplot(111, projection='3d')
        self.points_canvas = FigureCanvasTkAgg(self.points_fig, master=self.points_window)
        self.points_canvas.draw()
        self.points_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)
        self.points_window.withdraw()

        self.points = []
        self.position = (None, None, None)
        self.bounds = None

    def set_logger_handler(self, logger_handler):
        self.logger.addHandler(logger_handler)

    def set_scan_bounds(self, x1, x2, y1, y2, z1, z2):
        self.bounds = (x1, x2, y1, y2, z1, z2)
        self.update_points_plot()

    def parse_args(self, argv):
        parser = argparse.ArgumentParser(description="Simple demo to acquire spectra from command-line interface")
        parser.add_argument("--log-level",           type=str, default="INFO", help="logging level [DEBUG,INFO,WARNING,ERROR,CRITICAL]")
        parser.add_argument("--integration-time-ms", type=int, default=10,     help="integration time (ms, default 10)")
        parser.add_argument("--scans-to-average",    type=int, default=1,      help="scans to average (default 1)")
        parser.add_argument("--boxcar-half-width",   type=int, default=0,      help="boxcar half-width (default 0)")
        parser.add_argument("--delay-ms",            type=int, default=1000,   help="delay between integrations (ms, default 1000)")
        parser.add_argument("--outfile",             type=str, default=None,   help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("--max",                 type=int, default=0,      help="max spectra to acquire (default 0, unlimited)")
        parser.add_argument("--non-blocking",        action="store_true",      help="non-blocking USB interface (WasatchDeviceWrapper instead of WasatchDevice)")
        parser.add_argument("--ascii-art",           action="store_true",      help="graph spectra in ASCII")
        parser.add_argument("--version",             action="store_true",      help="display Wasatch.PY version and exit")

        # parse argv into dict
        args = parser.parse_args(argv[1:])
        if args.version:
            print("Wasatch.PY %s" % wasatch.__version__)
            sys.exit(0)

        # normalize log level
        args.log_level = args.log_level.upper()
        if not re.match("^(DEBUG|INFO|ERROR|WARNING|CRITICAL)$", args.log_level):
            print("Invalid log level: %s (defaulting to INFO)" % args.log_level)
            args.log_level = "INFO"

        return args


    def connect(self):
        """ If the current device is disconnected, and there is a new device,
            attempt to connect to it. """

        if self.device is not None:
            return

        if self.bus is None:
            print("instantiating WasatchBus")
            self.bus = WasatchBus(use_sim = False)

        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return

        device_id = self.bus.device_ids[0]
        print("connect: trying to connect to %s", device_id)
        device_id.device_type = RealUSBDevice(device_id)

        if self.args.non_blocking:
            print("instantiating WasatchDeviceWrapper (non-blocking)")
            device = WasatchDeviceWrapper(
                device_id = device_id,
                log_queue = self.logger.log_queue,
                log_level = self.args.log_level)
        else:
            print("instantiating WasatchDevice (blocking)")
            if device_id.vid == 0x24aa:
                device = WasatchDevice(device_id)
            else:
                device = OceanDevice(device_id)

        ok = device.connect()
        if not ok:
            print("connect: can't connect to %s", device_id)
            return

        print("connect: device connected")

        self.device = device
        self.reading_count = 0

        return device

    def run(self, type):
        self.type = type
        if self.device is None:
            print("Not connected to spectrometer")
            return False

        # apply initial settings
        self.device.change_setting("integration_time_ms", self.args.integration_time_ms)
        self.device.change_setting("scans_to_average", self.args.scans_to_average)
        self.device.change_setting("detector_tec_enable", True)

        start_time = datetime.datetime.now()
        self.attempt_reading()
        end_time = datetime.datetime.now()

      
        # compute how much longer we should wait before the next reading
        reading_time_ms = int((end_time - start_time).microseconds / 1000)
        sleep_ms = self.args.delay_ms - reading_time_ms
        if sleep_ms > 0:
            print("sleeping %d ms (%d ms already passed)", sleep_ms, reading_time_ms)
            try:
                time.sleep(float(sleep_ms) / 1000)
            except:
                pass
        return True

    def run_with_position(self, label, x, y, z):
        self.position = (x, y, z)
        return self.run(label)

    def attempt_reading(self):
        try:
            reading_response = self.acquire_reading()
        except Exception as exc:
            print("attempt_reading caught exception", exc_info=1)
            return

        if isinstance(reading_response.data, bool):
            if reading_response.data:
                print("received poison-pill, exiting")
                return
            else:
                print("no reading available")
                return

        if reading_response.data.failure:
            return

        self.process_reading(reading_response.data)

    def acquire_reading(self):
        while True:
            reading = self.device.acquire_data()
            if reading is None:
                print("waiting on next reading")
            else:
                return reading

    def process_reading(self, reading):
        if self.args.scans_to_average > 1 and not reading.averaged:
            return

        self.reading_count += 1

        if self.args.boxcar_half_width > 0:
            spectrum = utils.apply_boxcar(reading.spectrum, self.args.boxcar_half_width)
        else:
            spectrum = reading.spectrum

        if self.args.ascii_art:
            print("\n".join(wasatch.utils.ascii_spectrum(spectrum, rows=20, cols=80, x_axis=self.device.settings.wavelengths, x_unit="nm")))
        else:
            spectrum_min = numpy.amin(spectrum)
            spectrum_max = numpy.amax(spectrum)
            spectrum_avg = numpy.mean(spectrum)
            spectrum_std = numpy.std (spectrum)
            size_in_bytes = psutil.Process(os.getpid()).memory_info().rss

            print("Reading: %10d  Detector: %5.2f degC  Min: %8.2f  Max: %8.2f  Avg: %8.2f  StdDev: %8.2f  Memory: %11d" % (
                self.reading_count,
                reading.detector_temperature_degC,
                spectrum_min,
                spectrum_max,
                spectrum_avg,
                spectrum_std,
                size_in_bytes))
            print("%s", str(reading))

        # if self.type == "light":
        #     self.light_spectrum = spectrum
        # elif self.type != "dark" and self.light_spectrum is not None:
        #     # spectrum -= self.light_spectrum
        #     spectrum = numpy.subtract( spectrum,self.light_spectrum)

        # print(spectrum)

        if self.outfile:
            x, y, z = self.position
            self.outfile.write("%s;%s;%s;%s;%.2f;%s\n" % (
                self.type,
                format(x, ".2f") if x is not None else "",
                format(y, ".2f") if y is not None else "",
                format(z, ".2f") if z is not None else "",
                reading.detector_temperature_degC,
                ";".join(format(x, ".2f") for x in spectrum)))

        if None not in self.position:
            self.points.append(self.position)
            self.update_points_plot()

        self.draw_graph(spectrum)
        return

    ################################################################################
    # my_function
    ################################################################################

    def draw_graph(self, spectrum):
        # Clear previous plot
        self.ax.clear()
        self.ax.plot(self.device.settings.wavelengths, spectrum)
        self.canvas.draw()

    def set_output_file_path(self, outfile_path):
        self.args.outfile = outfile_path

        if self.outfile:
            try:
                self.outfile.close()
            except Exception as e:
                print("Error closing previous outfile: %s", str(e))

        try:
            file_exists = os.path.isfile(outfile_path)
            file_has_data = file_exists and os.path.getsize(outfile_path) > 0

            if file_has_data:
                self.outfile = open(outfile_path, "a")  
            else:
                self.outfile = open(outfile_path, "w")
                self.outfile.write("type;x;y;z;temp;%s\n" % ";".join(format(x, ".2f") for x in self.device.settings.wavelengths))
            
            print('Filepath set to: %s', outfile_path)
        except Exception as e:
            print("Error initializing %s: %s", outfile_path, str(e))
            self.outfile = None


    def set_integration_time(self, integration_time_ms):
        self.args.integration_time_ms = integration_time_ms
        print('Inetration time set to %i ms', integration_time_ms)

    def set_scans_to_average(self, scans_to_average):
        self.args.scans_to_average = scans_to_average
        print('Scans to average set to %i', scans_to_average)

    def set_boxcar_half_width(self, boxcar_half_width):
        self.args.boxcar_half_width = boxcar_half_width
        print('Boxcar half width set to %i', boxcar_half_width)

    def set_delay_ms(self, delay_ms):
        self.args.delay_ms = delay_ms
        print('Delay set to %i ms', delay_ms)

    def set_max_spectra(self, max_spectra):
        self.args.max = max_spectra
        print('Max spectra set to %i', max_spectra)

    def init_file(self):
        if self.args.outfile:
            try:
                file_exists = os.path.isfile(self.args.outfile)
                file_has_data = file_exists and os.path.getsize(self.args.outfile) > 0

                if file_has_data:
                    self.outfile = open(self.args.outfile, "a")  
                else:
                    self.outfile = open(self.args.outfile, "w")
                    self.outfile.write("type;x;y;z;temp;%s\n" % ";".join(format(x, ".2f") for x in self.device.settings.wavelengths))

            except Exception as e:
                print(f"Error initializing {self.args.outfile}: {e}")
                self.outfile = None

    def close_file(self):
        if self.args.outfile:
            self.outfile.close()


    def init_file_without_header(self):
         if self.args.outfile:
            try:
                self.outfile = open(self.args.outfile, "w")
            except:
                print("Error initializing %s", self.args.outfile)
                self.outfile = None

    def update_points_plot(self):
        self.points_ax.clear()
        if self.bounds:
            import itertools
            x1, x2, y1, y2, z1, z2 = self.bounds
            xs = [x1, x2]
            ys = [y1, y2]
            zs = [z1, z2]
            corners = list(itertools.product(xs, ys, zs))
            edges = [
                (0,1),(0,2),(2,3),(1,3),
                (4,5),(4,6),(6,7),(5,7),
                (0,4),(1,5),(2,6),(3,7)
            ]
            for e in edges:
                self.points_ax.plot(
                    [corners[e[0]][0], corners[e[1]][0]],
                    [corners[e[0]][1], corners[e[1]][1]],
                    [corners[e[0]][2], corners[e[1]][2]],
                    color='black'
                )
            self.points_ax.set_xlim(min(xs), max(xs))
            self.points_ax.set_ylim(min(ys), max(ys))
            self.points_ax.set_zlim(min(zs), max(zs))

        if self.points:
            xs, ys, zs = zip(*self.points)
            self.points_ax.scatter(xs, ys, zs, c='red', marker='o')

        self.points_canvas.draw()

    def toggle_plot(self):
        if self.graph_window.winfo_ismapped():
            self.graph_window.withdraw()  # Hide plot
        else:
            self.graph_window.deiconify()  # Show plot

    def toggle_points_window(self):
        if self.points_window.winfo_ismapped():
            self.points_window.withdraw()
        else:
            self.update_points_plot()
            self.points_window.deiconify()

def signal_handler(signal, frame):
    print('\rInterrupted by Ctrl-C...shutting down', end=' ')
    clean_shutdown()

def clean_shutdown():
    print("Exiting")
    if demo:
        if demo.args and demo.args.non_blocking and demo.device:
            print("closing background thread")
            demo.device.disconnect()

        if demo.logger:
            print("closing logger")
            print(None)
            demo.logger.close()
            time.sleep(1)
            applog.explicit_log_close()

        if demo.outfile:
            print("closing outfile")
            demo.outfile.close()
    sys.exit()

demo = None
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    demo = Wasatch(sys.argv)
    if demo.connect():
        print("Press Control-Break to interrupt...")
        demo.run()

    clean_shutdown()