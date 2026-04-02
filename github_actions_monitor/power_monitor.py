"""
power_monitor.py — Windows sleep/wake event detection via WM_POWERBROADCAST.

Creates a hidden message-only window on a background daemon thread.  When the
OS is about to suspend (PBT_APMSUSPEND) it sets *sleep_event* so the polling
loop can skip the next poll.  On resume (PBT_APMRESUMEAUTOMATIC) it clears
*sleep_event* and optionally fires *poll_event* so the tray app refreshes
status immediately after the machine wakes.

Only active on Windows (sys.platform == "win32").  On other platforms
``start_power_monitor`` is a no-op that returns immediately.
"""

from __future__ import annotations

import logging
import sys
import threading

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows constants
# ---------------------------------------------------------------------------

_WM_POWERBROADCAST = 0x0218
_PBT_APMSUSPEND = 0x0004          # system is about to suspend
_PBT_APMRESUMEAUTOMATIC = 0x0012  # system has resumed from sleep
_WM_DESTROY = 0x0002


def start_power_monitor(
    sleep_event: threading.Event,
    poll_event: threading.Event,
) -> None:
    """
    Start a background daemon thread that listens for Windows power events.

    Parameters
    ----------
    sleep_event:
        Set when the system is about to suspend; cleared on resume.
        The polling loop should skip polls while this is set.
    poll_event:
        Fired on resume so an immediate refresh poll is triggered.
    """
    if sys.platform != "win32":
        logger.debug("power_monitor: not on Windows, skipping.")
        return

    thread = threading.Thread(
        target=_run_message_loop,
        args=(sleep_event, poll_event),
        name="power-monitor",
        daemon=True,
    )
    thread.start()
    logger.debug("power_monitor: daemon thread started.")


def _run_message_loop(
    sleep_event: threading.Event,
    poll_event: threading.Event,
) -> None:
    """Message pump running on the power-monitor daemon thread."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Window procedure callback type
    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long,       # return value
        ctypes.wintypes.HWND,
        ctypes.c_uint,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )

    def _wnd_proc(hwnd, msg, wparam, lparam):
        if msg == _WM_POWERBROADCAST:
            if wparam == _PBT_APMSUSPEND:
                logger.info("power_monitor: system suspending — polling paused.")
                sleep_event.set()
            elif wparam == _PBT_APMRESUMEAUTOMATIC:
                logger.info("power_monitor: system resumed — triggering poll.")
                sleep_event.clear()
                poll_event.set()
        elif msg == _WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wnd_proc_cb = WNDPROC(_wnd_proc)

    hinstance = kernel32.GetModuleHandleW(None)
    class_name = "GHMonitorPowerWnd"

    # WNDCLASSW structure
    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style",          ctypes.c_uint),
            ("lpfnWndProc",    WNDPROC),
            ("cbClsExtra",     ctypes.c_int),
            ("cbWndExtra",     ctypes.c_int),
            ("hInstance",      ctypes.wintypes.HINSTANCE),
            ("hIcon",          ctypes.wintypes.HICON),
            ("hCursor",        ctypes.wintypes.HANDLE),
            ("hbrBackground",  ctypes.wintypes.HBRUSH),
            ("lpszMenuName",   ctypes.wintypes.LPCWSTR),
            ("lpszClassName",  ctypes.wintypes.LPCWSTR),
        ]

    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc_cb
    wc.hInstance = hinstance
    wc.lpszClassName = class_name

    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = kernel32.GetLastError()
        # ERROR_CLASS_ALREADY_EXISTS (1410) is harmless on rapid restarts
        if err != 1410:
            logger.error("power_monitor: RegisterClassW failed (error %d).", err)
            return

    # HWND_MESSAGE (-3) creates a message-only window — no desktop presence
    HWND_MESSAGE = ctypes.wintypes.HWND(-3)
    hwnd = user32.CreateWindowExW(
        0,           # dwExStyle
        class_name,  # lpClassName
        None,        # lpWindowName
        0,           # dwStyle
        0, 0, 0, 0,  # x, y, w, h
        HWND_MESSAGE,
        None,        # hMenu
        hinstance,
        None,        # lpParam
    )

    if not hwnd:
        logger.error(
            "power_monitor: CreateWindowExW failed (error %d).",
            kernel32.GetLastError(),
        )
        return

    logger.debug("power_monitor: message-only window created (hwnd=%s).", hwnd)

    # Standard Windows message pump
    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd",    ctypes.wintypes.HWND),
            ("message", ctypes.c_uint),
            ("wParam",  ctypes.wintypes.WPARAM),
            ("lParam",  ctypes.wintypes.LPARAM),
            ("time",    ctypes.c_ulong),
            ("pt",      ctypes.wintypes.POINT),
        ]

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    logger.debug("power_monitor: message loop exited.")
