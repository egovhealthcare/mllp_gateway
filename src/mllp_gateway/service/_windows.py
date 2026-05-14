"""Windows scheduled task management via schtasks."""

import subprocess

from mllp_gateway.service.common import SERVICE_NAME, _get_executable

_TASK = SERVICE_NAME


def install() -> None:
    exe = _get_executable()
    cmd_str = subprocess.list2cmdline([*exe, "run"])
    try:
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                _TASK,
                "/TR",
                cmd_str,
                "/SC",
                "ONLOGON",
                "/RL",
                "HIGHEST",
                "/F",
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create scheduled task (are you running as admin?): "
            f"{exc.stderr.decode().strip()}"
        ) from exc

    subprocess.run(["schtasks", "/Run", "/TN", _TASK], capture_output=True)
    print("Service installed (Windows scheduled task at logon).")


def uninstall() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", _TASK, "/F"], capture_output=True)
    print("Service removed.")


def status() -> None:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK], capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print("Service is not installed.")


def ensure() -> None:
    result = subprocess.run(["schtasks", "/Query", "/TN", _TASK], capture_output=True)
    if result.returncode != 0:
        install()
        return
    subprocess.run(["schtasks", "/Run", "/TN", _TASK], capture_output=True)
    print("MLLP Gateway service started.")
