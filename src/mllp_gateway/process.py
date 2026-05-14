"""Process lifecycle: instance locking, restart, and console management."""

import logging
import os
import subprocess
import sys
import time

from mllp_gateway.config import APP_DIR

__all__ = [
    "acquire_instance_lock",
    "hide_console",
    "is_frozen",
    "owns_console",
    "release_instance_lock",
    "restart_process",
]

logger = logging.getLogger(__name__)

# Held at module level to prevent garbage collection from releasing the
# underlying OS lock (mutex on Windows, flock fd on Unix).
_instance_mutex = None


def is_frozen() -> bool:
    """True when running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def release_instance_lock() -> None:
    global _instance_mutex
    if _instance_mutex is None:
        return
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.kernel32.ReleaseMutex(_instance_mutex)
            ctypes.windll.kernel32.CloseHandle(_instance_mutex)
        except Exception:
            pass
    else:
        try:
            _instance_mutex.close()
        except Exception:
            pass
    _instance_mutex = None


def restart_process() -> None:
    """Replace the current process with a fresh instance.

    On Unix this calls ``os.execv`` and **does not return**.
    On Windows a detached child is spawned and the parent exits.
    """
    release_instance_lock()

    env = os.environ.copy()

    if is_frozen():
        # PyInstaller 6.9+ treats subprocesses spawned via sys.executable
        # as worker processes that reuse the parent's temp dir.  Setting
        # PYINSTALLER_RESET_ENVIRONMENT tells the new bootloader to unpack
        # its own independent temp dir so it survives the parent exiting.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    if sys.platform == "win32":
        if is_frozen():
            cmd_args = [sys.executable, *sys.argv[1:]]
        else:
            cmd_args = [sys.executable, *sys.argv]

        CREATE_NEW_CONSOLE = 0x00000010
        CREATE_NEW_PROCESS_GROUP = 0x00000200

        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE

        try:
            p = subprocess.Popen(
                cmd_args,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP,
                startupinfo=si,
            )
            logger.info("restart: spawned child pid=%d", p.pid)
        except Exception:
            logger.exception("restart: spawn failed")
            return

        sys.exit(0)
    else:
        if is_frozen():
            args = list(sys.argv)
        else:
            args = [sys.executable] + list(sys.argv)
        os.execv(args[0], args)


def acquire_instance_lock() -> bool:
    """Try to acquire a system-wide single-instance lock.

    Retries for up to 5 seconds to allow a previous instance to shut down
    gracefully.  Returns True if the lock was acquired, False if another
    instance is running.
    """
    global _instance_mutex
    # Retry for up to 5s in case a previous instance is shutting down.
    deadline = time.monotonic() + 5.0
    while True:
        if sys.platform == "win32":
            import ctypes

            # CreateMutexW with bInitialOwner=True. Error 183
            # (ERROR_ALREADY_EXISTS) means another instance holds the mutex.
            _instance_mutex = ctypes.windll.kernel32.CreateMutexW(
                None, True, "Global\\MLLPGateway_SingleInstance"
            )
            if ctypes.windll.kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
                return True
            ctypes.windll.kernel32.CloseHandle(_instance_mutex)
            _instance_mutex = None
        else:
            import fcntl

            # Non-blocking exclusive file lock; fails immediately if
            # another process holds it.
            lock_path = APP_DIR / ".lock"
            APP_DIR.mkdir(parents=True, exist_ok=True)
            _instance_mutex = open(lock_path, "w")
            try:
                fcntl.flock(_instance_mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                _instance_mutex.close()
                _instance_mutex = None

        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def owns_console() -> bool:
    """True when this is the only process attached to its Windows console."""
    if sys.platform != "win32":
        return False
    import ctypes

    pids = (ctypes.c_ulong * 16)()
    return ctypes.windll.kernel32.GetConsoleProcessList(pids, 16) <= 1


def hide_console() -> None:
    """Hide the Windows console window (no-op on other platforms)."""
    if sys.platform != "win32":
        return
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
