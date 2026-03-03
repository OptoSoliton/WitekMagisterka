from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
import traceback


def configure_crash_logging() -> pathlib.Path:
    log_dir = pathlib.Path(os.getcwd()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    crash_log = log_dir / "app.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(crash_log, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        logging.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    def _thread_excepthook(args):
        logging.critical(
            "Unhandled thread exception in %s", args.thread.name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _sys_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_excepthook

    return crash_log


def report_tk_exception(exc_type, exc_value, exc_tb):
    logging.critical(
        "Unhandled Tkinter callback exception",
        exc_info=(exc_type, exc_value, exc_tb),
    )
