import tkinter as tk
from tkinter import ttk

PADX = 3
PADY = 2


class MeasurementWizard(tk.Toplevel):
    """Step-by-step wizard guiding through measurement setup."""

    def __init__(self, gui):
        super().__init__(gui.root)
        self.gui = gui
        self.title("Measurement wizard")
        self.geometry("600x400")
        self.steps = []
        self.current = 0
        self.container = ttk.Frame(self)
        self.container.pack(fill=tk.BOTH, expand=True)
        self._build_steps()

        nav = ttk.Frame(self)
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="Back", command=self._prev)
        self.back_btn.grid(row=0, column=0, padx=PADX, pady=PADY)
        self.next_btn = ttk.Button(nav, text="Next", command=self._next)
        self.next_btn.grid(row=0, column=1, padx=PADX, pady=PADY)
        self._show()

    def _build_steps(self):
        self.steps = [
            self._connections_step(),
            self._motion_step(),
            self._spectrometer_step(),
            self._references_step(),
            self._grid_step(),
            self._run_step(),
        ]

    def _show(self):
        for frame in self.steps:
            frame.pack_forget()
        self.steps[self.current].pack(fill=tk.BOTH, expand=True)
        state = "normal" if self.current > 0 else "disabled"
        self.back_btn.config(state=state)
        text = "Finish" if self.current == len(self.steps) - 1 else "Next"
        self.next_btn.config(text=text)

    def _next(self):
        if self.current < len(self.steps) - 1:
            self.current += 1
            self._show()
        else:
            self.destroy()

    def _prev(self):
        if self.current > 0:
            self.current -= 1
            self._show()

    # ----- Step 1 -----
    def _connections_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="1) Connections and file path",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, pady=(0, PADY))
        ttk.Label(frame, text="CNC Port:").grid(row=1, column=0, padx=PADX, pady=PADY, sticky="w")
        self.port_cb = ttk.Combobox(frame, values=self.gui.serial_ports)
        self.port_cb.grid(row=1, column=1, padx=PADX, pady=PADY, sticky="ew")
        ttk.Button(frame, text="Connect", command=self._connect_cnc).grid(
            row=1, column=2, padx=PADX, pady=PADY
        )
        ttk.Button(
            frame,
            text="Connect spectrometer",
            command=self.gui.connect_spectrometer,
        ).grid(row=2, column=0, columnspan=3, padx=PADX, pady=PADY, sticky="ew")
        ttk.Label(
            frame,
            text="Spectrometer optional – skip for wizard test",
            wraplength=560,
        ).grid(row=3, column=0, columnspan=3, padx=PADX, pady=(0, PADY), sticky="w")

        ttk.Button(
            frame,
            text="Browse CSV",
            command=self.gui.browse_file_path,
        ).grid(row=4, column=0, columnspan=3, padx=PADX, pady=PADY, sticky="ew")

        frame.grid_columnconfigure(1, weight=1)
        return frame

    def _connect_cnc(self):
        port = self.port_cb.get()
        if port:
            self.gui.serial_port_combobox.set(port)
        self.gui.toggle_connection()

    # ----- Step 2 -----
    def _motion_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="2) Motion and area",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=4, pady=(0, PADY))
        ttk.Label(frame, text="Step:").grid(row=1, column=0, padx=PADX, pady=PADY)
        self.step_entry = ttk.Entry(frame)
        self.step_entry.insert(0, self.gui.step_entry.get())
        self.step_entry.grid(row=1, column=1, padx=PADX, pady=PADY)
        ttk.Label(frame, text="Speed:").grid(row=1, column=2, padx=PADX, pady=PADY)
        self.speed_entry = ttk.Entry(frame)
        self.speed_entry.insert(0, self.gui.speed_entry.get())
        self.speed_entry.grid(row=1, column=3, padx=PADX, pady=PADY)
        ttk.Button(frame, text="Apply", command=self._apply_motion).grid(
            row=2, column=0, columnspan=4, padx=PADX, pady=PADY, sticky="ew"
        )
        ttk.Button(frame, text="Set 0.0", command=self.gui.set_position_zero).grid(
            row=3, column=0, padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Goto 0.0", command=self.gui.move_to_zero).grid(
            row=3, column=1, padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Set 1", command=lambda: self.gui.set_position(1)).grid(
            row=4, column=0, padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Set 2", command=lambda: self.gui.set_position(2)).grid(
            row=4, column=1, padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Set 4", command=lambda: self.gui.set_position(4)).grid(
            row=4, column=2, padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Test", command=self.gui.test_positions).grid(
            row=4, column=3, padx=PADX, pady=PADY
        )
        return frame

    def _apply_motion(self):
        self.gui.step_entry.delete(0, tk.END)
        self.gui.step_entry.insert(0, self.step_entry.get())
        self.gui.speed_entry.delete(0, tk.END)
        self.gui.speed_entry.insert(0, self.speed_entry.get())

    # ----- Step 3 -----
    def _spectrometer_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="3) Spectrometer parameters",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=4, pady=(0, PADY))
        ttk.Label(frame, text="IT (ms):").grid(row=1, column=0, padx=PADX, pady=PADY)
        self.it_entry = ttk.Entry(frame)
        self.it_entry.insert(0, self.gui.integration_time_entry.get())
        self.it_entry.grid(row=1, column=1, padx=PADX, pady=PADY)
        ttk.Label(frame, text="AVG:").grid(row=1, column=2, padx=PADX, pady=PADY)
        self.avg_entry = ttk.Entry(frame)
        self.avg_entry.insert(0, self.gui.scans_to_average_entry.get())
        self.avg_entry.grid(row=1, column=3, padx=PADX, pady=PADY)
        ttk.Label(frame, text="Boxcar:").grid(row=2, column=0, padx=PADX, pady=PADY)
        self.boxcar_entry = ttk.Entry(frame)
        self.boxcar_entry.insert(0, self.gui.boxcar_half_width_entry.get())
        self.boxcar_entry.grid(row=2, column=1, padx=PADX, pady=PADY)
        ttk.Label(frame, text="Delay (ms):").grid(row=2, column=2, padx=PADX, pady=PADY)
        self.delay_entry = ttk.Entry(frame)
        self.delay_entry.insert(0, self.gui.delay_ms_entry.get())
        self.delay_entry.grid(row=2, column=3, padx=PADX, pady=PADY)
        ttk.Button(frame, text="Apply", command=self._apply_spectrometer).grid(
            row=3, column=0, columnspan=4, padx=PADX, pady=PADY, sticky="ew"
        )
        ttk.Label(
            frame,
            text=(
                "IT raises signal and time; AVG improves SNR; "
                "Boxcar smooths noise; Delay enforces minimum interval."
            ),
            wraplength=560,
            justify="left",
        ).grid(row=4, column=0, columnspan=4, padx=PADX, pady=PADY, sticky="w")

        return frame

    def _apply_spectrometer(self):
        self.gui.integration_time_entry.delete(0, tk.END)
        self.gui.integration_time_entry.insert(0, self.it_entry.get())
        self.gui.set_integration_time()
        self.gui.scans_to_average_entry.delete(0, tk.END)
        self.gui.scans_to_average_entry.insert(0, self.avg_entry.get())
        self.gui.set_scans_to_average()
        self.gui.boxcar_half_width_entry.delete(0, tk.END)
        self.gui.boxcar_half_width_entry.insert(0, self.boxcar_entry.get())
        self.gui.set_boxcar_half_width()
        self.gui.delay_ms_entry.delete(0, tk.END)
        self.gui.delay_ms_entry.insert(0, self.delay_entry.get())
        self.gui.set_delay_ms()

    # ----- Step 4 -----
    def _references_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="4) References",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, pady=(0, PADY))
        ttk.Button(frame, text="Dark reference", command=self._run_dark).grid(
            row=1, column=0, columnspan=3, padx=PADX, pady=PADY, sticky="ew"
        )
        ttk.Button(frame, text="Light reference", command=self._run_light).grid(

            row=2, column=0, columnspan=3, padx=PADX, pady=PADY, sticky="ew"
        )
        ttk.Label(frame, text="Label:").grid(row=3, column=0, padx=PADX, pady=PADY)
        self.label_entry = ttk.Entry(frame)
        self.label_entry.grid(row=3, column=1, padx=PADX, pady=PADY)
        ttk.Button(frame, text="Run once", command=self._run_once).grid(
            row=3, column=2, padx=PADX, pady=PADY
        )
        ttk.Label(
            frame,
            text="Use references for later normalization; requires spectrometer.",
            wraplength=560,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, padx=PADX, pady=PADY, sticky="w")
        return frame

    def _run_dark(self):
        if self.gui.wasatch.device:
            self.gui.run_dark()
        else:
            self.gui.log("Spectrometer not connected")

    def _run_light(self):
        if self.gui.wasatch.device:
            self.gui.run_light()
        else:
            self.gui.log("Spectrometer not connected")

    def _run_once(self):
        # Reuse label entry for run-once label
        self.gui.run_once_label = self.label_entry
        if self.gui.wasatch.device:
            self.gui.start_measurement_once()
        else:
            self.gui.log("Spectrometer not connected")


    # ----- Step 5 -----
    def _grid_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="5) Grid",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=4, pady=(0, PADY))
        ttk.Label(frame, text="Samples X:").grid(row=1, column=0, padx=PADX, pady=PADY)
        self.sx_entry = ttk.Entry(frame)
        self.sx_entry.insert(0, self.gui.wasatch_samples_countX_entry.get())
        self.sx_entry.grid(row=1, column=1, padx=PADX, pady=PADY)
        ttk.Label(frame, text="Samples Y:").grid(row=1, column=2, padx=PADX, pady=PADY)
        self.sy_entry = ttk.Entry(frame)
        self.sy_entry.insert(0, self.gui.wasatch_samples_countY_entry.get())
        self.sy_entry.grid(row=1, column=3, padx=PADX, pady=PADY)
        ttk.Button(frame, text="Apply", command=self._apply_grid).grid(
            row=2, column=0, columnspan=4, padx=PADX, pady=PADY, sticky="ew"
        )
        return frame

    def _apply_grid(self):
        self.gui.wasatch_samples_countX_entry.delete(0, tk.END)
        self.gui.wasatch_samples_countX_entry.insert(0, self.sx_entry.get())
        self.gui.wasatch_samples_countY_entry.delete(0, tk.END)
        self.gui.wasatch_samples_countY_entry.insert(0, self.sy_entry.get())
        self.gui.calculate_predicted_points()

    # ----- Step 6 -----
    def _run_step(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text="6) Run",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(pady=PADY)
        ttk.Button(frame, text="Run area", command=self.gui.start_measurement).pack(
            fill="x", padx=PADX, pady=PADY
        )
        ttk.Button(frame, text="Stop", command=self.gui.stop_measurement).pack(
            fill="x", padx=PADX, pady=PADY
        )
        return frame

