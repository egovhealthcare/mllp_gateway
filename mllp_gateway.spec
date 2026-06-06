#!/usr/bin/env python
import sys
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

datas, binaries, hiddenimports = collect_all("mllp_gateway")

# Native extensions: submodules + shared libs (collect_all pulls in bloat)
for pkg in ["aiohttp", "multidict", "yarl", "frozenlist", "cryptography", "PIL"]:
    hiddenimports += collect_submodules(pkg)
    binaries += collect_dynamic_libs(pkg)

# Pure Python
for pkg in ["aiosignal", "aiohappyeyeballs", "hl7", "joserfc", "tomli_w", "six", "pystray", "serial", "serial_asyncio"]:
    hiddenimports += collect_submodules(pkg)

# Platform-specific pystray backends
for pkg in {"darwin": ["objc", "AppKit", "Foundation", "PyObjCTools", "Quartz"],
             "linux": ["gi", "Xlib"]}.get(sys.platform, []):
    try:
        hiddenimports += collect_submodules(pkg)
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

a = Analysis(
    ["src/mllp_gateway/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
)

exe = EXE(
    PYZ(a.pure, a.zipped_data),
    a.scripts, a.binaries, a.zipfiles, a.datas,
    name="mllp-gateway",
    console=True,
    upx=True,
)
