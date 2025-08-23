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
import subprocess

# Dark palette shared with the GUI
APP_BG = '#0F1117'
TEXT_PRIMARY = '#E6E8EB'
GRID_COLOR = '#2A2F3A'
AXIS_COLOR = '#D1D5DB'
LINE_COLORS = ['#60A5FA', '#34D399', '#F472B6', '#A78BFA',
               '#F59E0B', '#F87171', '#22D3EE', '#93C5FD']

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
        log.info(f"Wasatch.PY version {wasatch.__version__}")
        self.root = root

        # Figure for spectrum plot (embedded by GUI)
        self.fig = Figure(figsize=(2.5, 1.25), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = None

        # Figure for measured and predicted points (embedded by GUI)
        self.points_fig = Figure(figsize=(2.5, 2.5), dpi=100)
        self.points_ax = self.points_fig.add_subplot(111, projection='3d')
        try:
            self.points_ax.set_proj_type('ortho')
        except Exception:
            pass
        try:
            from matplotlib import ticker as _ticker
            self.points_ax.yaxis.set_major_locator(_ticker.MaxNLocator(prune='lower'))
            self.points_ax.zaxis.set_major_locator(_ticker.MaxNLocator(prune='lower'))
        except Exception:
            pass
        self.points_ax.get_zaxis().set_visible(False)
        self.points_ax.set_zlabel('')
        self.points_ax.set_zticks([])
        self.points_canvas = None

        self.points = []
        self.scan_points = None
        self.predicted_points = None
        self.position = (None, None, None)
        self.bounds = None
        self.reading_count = 0
        self.points_zoomed = False
        self.marked_dots = []
        self.marked_labels = []

    def set_dark_mode(self, dark: bool):
        """Toggle matplotlib figures between light and dark themes."""
        if dark:
            bg = APP_BG
            fg = TEXT_PRIMARY
            grid = GRID_COLOR
            axis = AXIS_COLOR
            line_colors = LINE_COLORS
        else:
            bg = 'white'
            fg = 'black'
            grid = '#e0e0e0'
            axis = 'black'
            line_colors = None

        # spectrum plot
        self.fig.patch.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        for spine in self.ax.spines.values():
            spine.set_color(axis)
        self.ax.tick_params(colors=fg)
        self.ax.xaxis.label.set_color(axis)
        self.ax.yaxis.label.set_color(axis)
        if line_colors:
            self.ax.set_prop_cycle(color=line_colors)
        self.ax.grid(color=grid, alpha=0.25)

        # 3D points plot
        self.points_fig.patch.set_facecolor(bg)
        self.points_ax.set_facecolor(bg)
        try:
            self.points_ax.w_xaxis.set_pane_color((*self._hex_to_rgb(grid), 0.25))
            self.points_ax.w_yaxis.set_pane_color((*self._hex_to_rgb(grid), 0.25))
            self.points_ax.w_zaxis.set_pane_color((*self._hex_to_rgb(grid), 0.25))
        except Exception:
            pass
        self.points_ax.tick_params(colors=fg)
        self.points_ax.xaxis.label.set_color(axis)
        self.points_ax.yaxis.label.set_color(axis)
        self.points_ax.zaxis.label.set_color(axis)
        self.points_ax.grid(color=grid, alpha=0.25)

        if self.canvas:
            self.canvas.draw_idle()
        if self.points_canvas:
            self.points_canvas.draw_idle()

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16)/255 for i in (0, 2, 4))

    def set_logger_handler(self, logger_handler):
        """Attach an additional handler to the Wasatch logger.

        The underlying `applog.MainLogger` used by Wasatch.PY exposes its
        root logger via the ``root`` attribute rather than implementing the
        standard ``addHandler`` method.  Using ``root`` ensures compatibility
        while still allowing external handlers (like the GUI log window) to
        receive messages.  Fall back to the global logger if the expected
        attribute is missing.
        """

        try:
            # ``MainLogger`` provides access to the real ``logging.Logger`` via
            # the ``root`` attribute.
            self.logger.root.addHandler(logger_handler)
        except AttributeError:
            logging.getLogger().addHandler(logger_handler)

        # Also attach to this module's logger so direct ``log`` calls propagate
        # to the same handler.
        log.addHandler(logger_handler)

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

    def list_usb_devices(self):
        """Return a list of connected USB devices with basic details."""
        devices = []
        try:
            import usb.core, usb.util  # type: ignore
            for dev in usb.core.find(find_all=True):
                desc = f"VID={dev.idVendor:04x} PID={dev.idProduct:04x}"
                bus = getattr(dev, 'bus', None)
                addr = getattr(dev, 'address', None)
                if bus is not None and addr is not None:
                    desc += f" bus={bus} addr={addr}"
                try:
                    man = usb.util.get_string(dev, dev.iManufacturer) or ""
                    prod = usb.util.get_string(dev, dev.iProduct) or ""
                    if man or prod:
                        desc += f" {man} {prod}".strip()
                except Exception:
                    pass
                devices.append((desc, bus, addr, dev.idVendor, dev.idProduct))
        except Exception as e:
            log.exception("USB device listing failed: %s", e)
        return devices

    def connect(self, target=None):
        """Connect to a Wasatch spectrometer, optionally specifying a USB target."""
        if self.device is not None:
            return self.device

        if target:
            try:
                import usb.core, usb.util  # type: ignore
                dev = usb.core.find(
                    idVendor=target.get('vid'),
                    idProduct=target.get('pid'),
                    bus=target.get('bus'),
                    address=target.get('address'),
                )
                if dev:
                    usb_dev = RealUSBDevice(dev)
                    device = WasatchDevice(usb_dev)
                    ok = device.connect()
                    if ok:
                        self.device = device
                        log.info("Connected to selected USB device")
                        return device
            except Exception as e:
                log.exception("Direct USB connect failed: %s", e)

        if self.bus is None:
            log.debug("instantiating WasatchBus")
            self.bus = WasatchBus(use_sim=False)

        # refresh device list in case USB state changed
        self.bus.update()
        if not self.bus.device_ids:
            log.info("No Wasatch USB spectrometers found.")
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

        log.info("Connecting to %s", device_id)
        ok = device.connect()
        if not ok:
            log.critical("connect: can't connect to %s", device_id)
            return None

        self.device = device
        self.reading_count = 0
        self.init_file()
        log.info("Spectrometer connected")
        return device

    def troubleshoot_connection(self, target=None):
        """Attempt a variety of low level recovery steps to connect."""
        log.info("Starting connection troubleshooting")

        # enumerate using WasatchBus
        try:
            self.bus = WasatchBus(use_sim=False)
            self.bus.update()
            log.info("Found %d USB devices", len(self.bus.device_ids))
            for dev in self.bus.device_ids:
                log.info("Device id: %s", dev)
        except Exception as e:
            log.exception("USB enumeration failed: %s", e)

        # enumerate with pyusb for raw details
        try:
            import usb.core, usb.util  # type: ignore
            devices = list(usb.core.find(find_all=True))
            log.info("pyusb detected %d USB devices", len(devices))
            for dev in devices:
                log.info(
                    "USB vid=0x%04x pid=0x%04x bus=%s addr=%s", dev.idVendor, dev.idProduct,
                    getattr(dev, 'bus', '?'), getattr(dev, 'address', '?')
                )
                if target and dev.idVendor == target.get('vid') and dev.idProduct == target.get('pid'):
                    try:
                        log.info("Attempting USB reset on targeted spectrometer")
                        if dev.is_kernel_driver_active(0):
                            log.info("Detaching kernel driver")
                            dev.detach_kernel_driver(0)
                        dev.reset()
                        dev.set_configuration()
                        cfg = dev.get_active_configuration()
                        intf = cfg[(0, 0)]
                        usb.util.claim_interface(dev, intf.bInterfaceNumber)
                        log.info("Claimed interface %d", intf.bInterfaceNumber)
                        usb.util.release_interface(dev, intf.bInterfaceNumber)
                        try:
                            dev.attach_kernel_driver(intf.bInterfaceNumber)
                        except Exception:
                            pass
                        usb.util.dispose_resources(dev)
                    except Exception as ex:
                        log.exception("USB reset failed: %s", ex)
        except Exception as e:
            log.exception("pyusb enumeration failed: %s", e)

        # system level diagnostics
        try:
            if sys.platform.startswith('linux'):
                out = subprocess.check_output(['lsusb']).decode(errors='ignore')
                log.info("lsusb output:\n%s", out)
            elif os.name == 'nt':
                cmd = ['powershell', '-Command',
                       'Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like "*VID_24AA*"}']
                out = subprocess.check_output(cmd, text=True)
                log.info("PnpDevice output:\n%s", out)
                try:
                    toggle = [
                        'Get-PnpDevice -InstanceId "USB\\VID_24AA*" | Disable-PnpDevice -Confirm:$false',
                        'Start-Sleep -Seconds 1',
                        'Get-PnpDevice -InstanceId "USB\\VID_24AA*" | Enable-PnpDevice -Confirm:$false'
                    ]
                    out = subprocess.check_output(['powershell', '-Command', ';'.join(toggle)], text=True)
                    log.info("USB port toggle output:\n%s", out)
                except Exception as ex:
                    log.exception("USB port toggle failed: %s", ex)
        except Exception as e:
            log.exception("System USB query failed: %s", e)

        # list available COM ports for manual selection
        try:
            import serial.tools.list_ports  # type: ignore
            ports = list(serial.tools.list_ports.comports())
            for p in ports:
                log.info("COM port %s %s", p.device, p.description)
        except Exception as e:
            log.exception("Serial port enumeration failed: %s", e)

        # attempt multiple connection methods
        methods = [
            ("WasatchDevice", WasatchDevice),
            ("WasatchDeviceWrapper", WasatchDeviceWrapper),
        ]
        for name, cls in methods:
            try:
                log.info("Attempting connection using %s", name)
                dev = cls(self.bus.device_ids[0])
                dev.connect()
                self.device = dev
                log.info("%s connection succeeded", name)
                return dev
            except Exception as e:
                log.exception("%s connection failed: %s", name, e)

        # final forced connection attempts
        device = self.force_connect(target=target)
        if device:
            return device

        log.info("Troubleshooting finished - no spectrometer connected")
        return None

    def force_connect(self, attempts: int = 3, delay: float = 1.0, target=None):
        """Repeatedly try to connect, resetting USB between attempts."""
        for i in range(attempts):
            log.info("Force-connect attempt %d/%d", i + 1, attempts)
            device = self.connect(target)
            if device:
                log.info("Force-connect succeeded on attempt %d", i + 1)
                return device
            try:
                import usb.core, usb.util  # type: ignore
                if target:
                    dev = usb.core.find(
                        idVendor=target.get('vid'),
                        idProduct=target.get('pid'),
                        bus=target.get('bus'),
                        address=target.get('address'),
                    )
                else:
                    dev = usb.core.find(idVendor=0x24aa)
                if dev:
                    log.info("Issuing USB reset to spectrometer")
                    dev.reset()
                    usb.util.dispose_resources(dev)
            except Exception as e:
                log.exception("USB reset attempt failed: %s", e)
            time.sleep(delay)
        log.info("Force-connect attempts exhausted")
        return None

    def run(self, label):
        if self.device is None:
            log.info("Not connected to spectrometer")
            return False

        self.type = label
        self.device.change_setting("integration_time_ms", self.args.integration_time_ms)
        self.device.change_setting("scans_to_average", self.args.scans_to_average)
        self.device.change_setting("detector_tec_enable", True)

        start_time = datetime.datetime.now()
        ok = self.attempt_reading()
        end_time = datetime.datetime.now()

        reading_time_ms = int((end_time - start_time).microseconds / 1000)
        sleep_ms = self.args.delay_ms - reading_time_ms
        if sleep_ms > 0:
            log.debug(f"sleeping {sleep_ms} ms ({reading_time_ms} ms already passed)")
            try:
                time.sleep(float(sleep_ms) / 1000)
            except Exception:
                pass
        return ok

    def run_with_position(self, label, x, y, z):
        self.position = (x, y, z)
        return self.run(label)

    def attempt_reading(self):
        try:
            reading_response = self.acquire_reading()
        except Exception as exc:
            log.error("attempt_reading exception: %s", exc, exc_info=True)
            return False

        if isinstance(reading_response.data, bool):
            if reading_response.data:
                log.warning("received poison-pill, exiting")
            else:
                log.warning("no reading available")
            return False

        if reading_response.data.failure:
            log.error("spectrometer returned failure")
            return False

        self.process_reading(reading_response.data)
        return True

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
        if not self.canvas:
            return

        def _do():
            self.ax.clear()
            self.ax.plot(self.device.settings.wavelengths, spectrum)
            self.canvas.draw()

        self.canvas.get_tk_widget().after(0, _do)

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
        print(f'Integration time set to {integration_time_ms} ms')

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

        def _do():
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
                # keep cube edges and points inside the axes
                xr = self.points_ax.get_xlim3d()[1] - self.points_ax.get_xlim3d()[0]
                yr = self.points_ax.get_ylim3d()[1] - self.points_ax.get_ylim3d()[0]
                zr = self.points_ax.get_zlim3d()[1] - self.points_ax.get_zlim3d()[0]
                self.points_ax.set_box_aspect([xr, yr, zr])
                self.points_ax.auto_scale_xyz(
                    self.points_ax.get_xlim3d(),
                    self.points_ax.get_ylim3d(),
                    self.points_ax.get_zlim3d(),
                )
                self.points_ax.invert_zaxis()

            if self.scan_points:
                colors = ['blue', 'green', 'magenta', 'orange', 'cyan']
                for idx, key in enumerate(['1','2','3','4','5']):
                    pt = self.scan_points.get(key)
                    if pt:
                        self.points_ax.scatter(
                            [pt['X']], [pt['Y']], [pt['Z']],
                            color=colors[idx], marker='^',
                            s=60,
                            label=f'Point {key}'
                        )

            if self.predicted_points:
                xs, ys, zs = zip(*self.predicted_points)
                self.points_ax.scatter(xs, ys, zs, c='gray', alpha=0.3, s=25)

            if self.points:
                xs, ys, zs = zip(*self.points)
                self.points_ax.scatter(xs, ys, zs, c='red', marker='o', s=40)

            if None not in self.position:
                self.points_ax.scatter([self.position[0]], [self.position[1]], [self.position[2]], c='red', s=80, alpha=0.6)

            self.points_ax.set_xlabel('X')
            self.points_ax.set_ylabel('Y')
            self.points_ax.set_zlabel('')
            self.points_ax.set_zticks([])
            if self.scan_points:
                handles, labels = self.points_ax.get_legend_handles_labels()
                if handles:
                    self.points_ax.legend(loc='best')
            xr = self.points_ax.get_xlim3d()[1] - self.points_ax.get_xlim3d()[0]
            yr = self.points_ax.get_ylim3d()[1] - self.points_ax.get_ylim3d()[0]
            zr = self.points_ax.get_zlim3d()[1] - self.points_ax.get_zlim3d()[0]
            if zr == 0:
                zr = 1
            self.points_ax.set_box_aspect([xr, yr, zr])
            self.points_canvas.draw()

        self.points_canvas.get_tk_widget().after(0, _do)

    def update_marked_points(self, positions):
        if not self.points_canvas:
            return

        def _do():
            for d in self.marked_dots:
                d.remove()
            for l in self.marked_labels:
                l.remove()
            self.marked_dots = []
            self.marked_labels = []
            for pos in positions:
                d = self.points_ax.scatter([pos['X']], [pos['Y']], [pos['Z']], color='red', marker='x')
                l = self.points_ax.text(pos['X'], pos['Y'], pos['Z'], str(pos['index']), color='red')
                self.marked_dots.append(d)
                self.marked_labels.append(l)
            self.points_canvas.draw()

        self.points_canvas.get_tk_widget().after(0, _do)

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
        self.fig.subplots_adjust(bottom=0.2)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

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
