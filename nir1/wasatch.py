import os
import re
import sys
import time
import datetime
import argparse
import numpy
import psutil
import wasatch
from wasatch import utils
from wasatch import applog
from wasatch.WasatchBus           import WasatchBus
from wasatch.OceanDevice          import OceanDevice
from wasatch.WasatchDevice        import WasatchDevice
from wasatch.WasatchDeviceWrapper import WasatchDeviceWrapper
from wasatch.RealUSBDevice        import RealUSBDevice
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
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
        print(f"Wasatch.PY version {wasatch.__version__}")
        self.root = root

        # Figure for spectrum plot (embedded by GUI)
        self.fig = Figure(figsize=(2.5, 2.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = None

        # Figure for measured and predicted points (embedded by GUI)
        self.points_fig = Figure(figsize=(2.5, 2.5), dpi=100)
        self.points_ax = self.points_fig.add_subplot(111, projection='3d')
        self.points_canvas = None

        self.points = []
        self.scan_points = None
        self.predicted_points = None
        self.position = (None, None, None)
        self.bounds = None
        self.reading_count = 0
        self.points_zoomed = False


    def set_logger_handler(self, logger_handler):
        self.logger.addHandler(logger_handler)

    def set_scan_bounds(self, x1, x2, y1, y2, z1, z2, points=None,
                        count_x=1, count_y=1, count_z=1,
                        progress_cb=None, log_cb=None):
        self.bounds = (x1, x2, y1, y2, z1, z2)
        self.scan_points = points
        self.predicted_points = []
        self.points_zoomed = False
        try:
            step_x = (x2 - x1) / (count_x - 1) if count_x > 1 else 0
            step_y = (y2 - y1) / (count_y - 1) if count_y > 1 else 0
            step_z = (z2 - z1) / (count_z - 1) if count_z > 1 else 0
            total = max(count_x * count_y * count_z, 1)
            counter = 0
            if log_cb:
                log_cb(
                    f"Bounds: x1={x1}, x2={x2}, y1={y1}, y2={y2}, z1={z1}, z2={z2}"
                )
                log_cb(
                    f"Step sizes: step_x={step_x}, step_y={step_y}, step_z={step_z}"
                )
            if progress_cb:
                progress_cb(0)
            for k in range(count_z):
                z = z1 + k * step_z
                for i in range(count_x):
                    x = x1 + i * step_x
                    for j in range(count_y):
                        y = y1 + j * step_y
                        self.predicted_points.append((x, y, z))
                        counter += 1
                        if progress_cb:
                            progress_cb(int(counter * 100 / total))
                        if log_cb:
                            log_cb(f"Generated {counter}/{total} points at x={x:.2f}, y={y:.2f}, z={z:.2f}")
            if progress_cb:
                progress_cb(100)
            if log_cb:
                log_cb("Finished generating points")
        except Exception:
            pass
        self.update_points_plot()

    def parse_args(self, argv):
        if argv is None:
            argv = sys.argv
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

        args = parser.parse_args(argv[1:])
        if args.version:
            print(f"Wasatch.PY {wasatch.__version__}")
            sys.exit(0)

        args.log_level = args.log_level.upper()
        if not re.match("^(DEBUG|INFO|ERROR|WARNING|CRITICAL)$", args.log_level):
            print(f"Invalid log level: {args.log_level} (defaulting to INFO)")
            args.log_level = "INFO"

        return args

    def connect(self):
        """Connect to the first available Wasatch spectrometer."""
        if self.device is not None:
            return self.device

        if self.bus is None:
            log.debug("instantiating WasatchBus")
            self.bus = WasatchBus(use_sim=False)

        # refresh device list in case USB state changed
        self.bus.update()
        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return None

        device_id = self.bus.device_ids[0]
        log.debug("connect: trying to connect to %s", device_id)
        device_id.device_type = RealUSBDevice(device_id)

        if self.args.non_blocking:
            log.debug("instantiating WasatchDeviceWrapper (non-blocking)")
            device = WasatchDeviceWrapper(
                device_id=device_id,
                log_queue=self.logger.log_queue,
                log_level=self.args.log_level,
            )
        else:
            log.debug("instantiating WasatchDevice (blocking)")
            if device_id.vid == 0x24aa:
                device = WasatchDevice(device_id)
            else:
                device = OceanDevice(device_id)

        ok = device.connect()
        if not ok:
            log.critical("connect: can't connect to %s", device_id)
            return None

        self.device = device
        self.reading_count = 0
        self.init_file()
        return device

    def run(self, label):
        if self.device is None:
            print("Not connected to spectrometer")
            return False

        self.type = label
        self.device.change_setting("integration_time_ms", self.args.integration_time_ms)
        self.device.change_setting("scans_to_average", self.args.scans_to_average)
        self.device.change_setting("detector_tec_enable", True)

        start_time = datetime.datetime.now()
        self.attempt_reading()
        end_time = datetime.datetime.now()

        reading_time_ms = int((end_time - start_time).microseconds / 1000)
        sleep_ms = self.args.delay_ms - reading_time_ms
        if sleep_ms > 0:
            print(f"sleeping {sleep_ms} ms ({reading_time_ms} ms already passed)")
            try:
                time.sleep(float(sleep_ms) / 1000)
            except Exception:
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
            if reading is not None:
                return reading
            print("waiting on next reading")
            time.sleep(0.05)

    def process_reading(self, reading):
        spectrum = reading.spectrum
        self.reading_count += 1

        spectrum_min = numpy.amin(spectrum)
        spectrum_max = numpy.amax(spectrum)
        spectrum_avg = numpy.mean(spectrum)
        spectrum_std = numpy.std(spectrum)
        size_in_bytes = psutil.Process(os.getpid()).memory_info().rss

        print("Reading: %10d  Detector: %5.2f degC  Min: %8.2f  Max: %8.2f  Avg: %8.2f  StdDev: %8.2f  Memory: %11d" % (
            self.reading_count,
            reading.detector_temperature_degC,
            spectrum_min,
            spectrum_max,
            spectrum_avg,
            spectrum_std,
            size_in_bytes))
        print(str(reading))

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
        base, ext = os.path.splitext(outfile_path)
        unique_path = outfile_path
        counter = 1
        while os.path.exists(unique_path):
            unique_path = f"{base}_{counter}{ext}"
            counter += 1
        self.args.outfile = unique_path
        if self.outfile:
            try:
                self.outfile.close()
            except Exception as e:
                print(f"Error closing previous outfile: {e}")
        self.outfile = None
        print(f'Filepath set to: {self.args.outfile}')


    def set_integration_time(self, integration_time_ms):
        self.args.integration_time_ms = integration_time_ms
        print(f'Inetration time set to {integration_time_ms} ms')

    def set_scans_to_average(self, scans_to_average):
        self.args.scans_to_average = scans_to_average
        print(f'Scans to average set to {scans_to_average}')

    def set_boxcar_half_width(self, boxcar_half_width):
        self.args.boxcar_half_width = boxcar_half_width
        print(f'Boxcar half width set to {boxcar_half_width}')

    def set_delay_ms(self, delay_ms):
        self.args.delay_ms = delay_ms
        print(f'Delay set to {delay_ms} ms')

    def set_max_spectra(self, max_spectra):
        self.args.max = max_spectra
        print(f'Max spectra set to {max_spectra}')

    def init_file(self):
        if not self.args.outfile:
            return
        try:
            file_exists = os.path.isfile(self.args.outfile)
            file_has_data = file_exists and os.path.getsize(self.args.outfile) > 0
            mode = "a" if file_has_data else "w"
            self.outfile = open(self.args.outfile, mode)
            if mode == "w" and getattr(self.device, "settings", None):
                wavelengths = self.device.settings.wavelengths
                self.outfile.write(
                    "type;x;y;z;temp;%s\n" % ";".join(f"{x:.2f}" for x in wavelengths)
                )
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
            except Exception:
                print(f"Error initializing {self.args.outfile}")
                self.outfile = None

    def update_points_plot(self):
        if not self.points_canvas:
            return
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
            if not self.points_zoomed:
                self.points_ax.set_xlim(min(xs), max(xs))
                self.points_ax.set_ylim(min(ys), max(ys))
                self.points_ax.set_zlim(min(zs), max(zs))
            self.points_ax.invert_zaxis()

        if self.scan_points:
            colors = ['blue', 'green', 'magenta', 'orange', 'cyan']
            for idx, key in enumerate(['1','2','3','4','5']):
                pt = self.scan_points.get(key)
                if pt:
                    self.points_ax.scatter([pt['X']], [pt['Y']], [pt['Z']], color=colors[idx], marker='^', label=f'Point {key}')

        if self.predicted_points:
            xs, ys, zs = zip(*self.predicted_points)
            self.points_ax.scatter(xs, ys, zs, c='gray', alpha=0.3, s=10)

        if self.points:
            xs, ys, zs = zip(*self.points)
            self.points_ax.scatter(xs, ys, zs, c='red', marker='o', s=20)

        if None not in self.position:
            self.points_ax.scatter([self.position[0]], [self.position[1]], [self.position[2]], c='red', s=80, alpha=0.6)

        self.points_ax.set_xlabel('X')
        self.points_ax.set_ylabel('Y')
        self.points_ax.set_zlabel('Z')
        if self.scan_points:
            self.points_ax.legend(loc='best')

        self.points_canvas.draw()

    def init_points_plot(self, frame):
        self.points_canvas = FigureCanvasTkAgg(self.points_fig, master=frame)
        self.points_canvas.draw()
        # use literal string to avoid depending on tkinter alias
        self.points_canvas.get_tk_widget().pack(fill="both", expand=False)

    def init_spectrum_plot(self, frame):
        if self.canvas:
            return
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.draw()
        # use literal string to avoid depending on tkinter alias
        self.canvas.get_tk_widget().pack(fill="both", expand=False)

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
