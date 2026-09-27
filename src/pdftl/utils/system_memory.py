# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/system_memory.py

"""Available memory and decoded image sizes, for memory budgets.

Every probe degrades to None rather than raising: callers fall back to a
fixed default budget on platforms where memory cannot be measured.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

MB = 1024 * 1024
DEFAULT_MAX_DECODED_MB = 512
DEFAULT_BUDGET_MB = 512
BUDGET_FRACTION = 0.5
MIN_BUDGET_MB = 128
MAX_BUDGET_MB = 4096
_MEMINFO = "/proc/meminfo"
_SELF_CGROUP = "/proc/self/cgroup"
_CGROUP_ROOT = "/sys/fs/cgroup"
_UNLIMITED = 1 << 60
_COMPONENTS = {
    "/DeviceGray": 1,
    "/CalGray": 1,
    "/Indexed": 1,
    "/Separation": 1,
    "/DeviceRGB": 3,
    "/CalRGB": 3,
    "/Lab": 3,
    "/DeviceCMYK": 4,
}
UNKNOWN_COMPONENTS = 4
IMAGE_MEMORY_HELP = (
    "Decoded images in flight are kept within half the available memory (or "
    "`PDFTL_IMAGE_MEMORY_MB`), and an image that would decode to more than "
    "`PDFTL_MAX_DECODED_MB` (default 512; 0 for no limit) is skipped with a warning."
)


def available_memory_bytes() -> int | None:
    """Memory the OS could give this process now without swapping; None if unknown."""
    host = _first(_from_psutil, _from_meminfo, _from_windows, _from_sysconf, _from_vm_stat)
    limit = _first(_cgroup_headroom)  # containers: the host figure ignores the cgroup cap
    known = [v for v in (host, limit) if v]
    return min(known) if known else None


def _first(*probes) -> int | None:
    for probe in probes:
        found = probe()
        if found:
            return found
    return None


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as f:
            text = f.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None  # cgroup v2 writes "max" for no limit


def _cgroup_headroom() -> int | None:
    """Smallest (limit - usage) over this process's cgroup v2 ancestry, or cgroup v1's."""
    try:
        with open(_SELF_CGROUP, encoding="ascii") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    rooms = []
    for line in lines:
        if not line.startswith("0::"):
            continue
        path = line[3:].rstrip("/")
        while True:
            base = f"{_CGROUP_ROOT}{path}"
            limit, used = _read_int(f"{base}/memory.max"), _read_int(f"{base}/memory.current")
            if limit is not None and used is not None and limit < _UNLIMITED:
                rooms.append(max(limit - used, 0))
            if not path:
                break
            path = path.rsplit("/", 1)[0]
    v1 = f"{_CGROUP_ROOT}/memory"
    limit, used = (
        _read_int(f"{v1}/memory.limit_in_bytes"),
        _read_int(f"{v1}/memory.usage_in_bytes"),
    )
    if limit is not None and used is not None and limit < _UNLIMITED:
        rooms.append(max(limit - used, 0))
    return min(rooms) if rooms else None


def _from_psutil() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.virtual_memory().available)
    except OSError:  # no /proc
        return None


def _from_meminfo() -> int | None:
    try:
        with open(_MEMINFO, encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _from_windows() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def _from_sysconf() -> int | None:
    names = getattr(os, "sysconf_names", {})
    if "SC_AVPHYS_PAGES" not in names or "SC_PAGE_SIZE" not in names:
        return None
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except OSError:
        return None


def _from_vm_stat() -> int | None:
    """macOS: free, inactive, speculative and purgeable pages are reclaimable."""
    if sys.platform != "darwin":
        return None
    try:
        run = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_vm_stat(run.stdout)


def parse_vm_stat(text: str) -> int | None:
    size = re.search(r"page size of (\d+) bytes", text)
    if not size:
        return None
    pages = 0
    for key in ("free", "inactive", "speculative", "purgeable"):
        found = re.search(rf"^Pages {key}:\s+(\d+)", text, re.MULTILINE)
        pages += int(found.group(1)) if found else 0
    return pages * int(size.group(1)) or None


def _env_mb(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number of megabytes", name, raw)
        return None


def image_job_budget_bytes() -> int:
    """Decoded bytes an image job may hold at once (PDFTL_IMAGE_MEMORY_MB overrides)."""
    override = _env_mb("PDFTL_IMAGE_MEMORY_MB")
    if override is not None and override > 0:
        return int(override * MB)
    available = available_memory_bytes()
    if available is None:
        return DEFAULT_BUDGET_MB * MB
    budget = available * BUDGET_FRACTION
    return int(min(max(budget, MIN_BUDGET_MB * MB), MAX_BUDGET_MB * MB))


def max_decoded_bytes() -> int | None:
    """Largest image to decode (PDFTL_MAX_DECODED_MB; 0 means no limit)."""
    override = _env_mb("PDFTL_MAX_DECODED_MB")
    mb = DEFAULT_MAX_DECODED_MB if override is None else override
    return int(mb * MB) if mb > 0 else None


def _components(colorspace) -> int:
    import pikepdf

    if isinstance(colorspace, pikepdf.Array) and len(colorspace) > 0:
        family = str(colorspace[0])
        if family == "/ICCBased":
            try:
                return int(colorspace[1].get("/N", UNKNOWN_COMPONENTS))
            except (AttributeError, TypeError, ValueError, IndexError):
                return UNKNOWN_COMPONENTS
        if family == "/DeviceN" and len(colorspace) > 1:
            return max(1, len(colorspace[1]))
        return _COMPONENTS.get(family, UNKNOWN_COMPONENTS)
    return _COMPONENTS.get(str(colorspace), UNKNOWN_COMPONENTS)


def decoded_image_bytes(xobj) -> int | None:
    """Bytes a decoded copy of this image (and its soft mask) takes; None if unknowable.

    At least one byte per sample, as Pillow stores them.
    """
    try:
        width, height = int(xobj.get("/Width")), int(xobj.get("/Height"))
        bpc = int(xobj.get("/BitsPerComponent", 8))
        if xobj.get("/ImageMask", False):
            components = 1
        else:
            components = _components(xobj.get("/ColorSpace", "/DeviceRGB"))
    except (AttributeError, TypeError, ValueError):  # a malformed image dictionary
        return None
    if width <= 0 or height <= 0:
        return None
    total = width * height * components * max(1, (bpc + 7) // 8)
    smask = xobj.get("/SMask")
    if smask is not None and smask is not xobj:
        total += decoded_image_bytes(smask) or 0
    return total


def too_large_to_decode(xobj, what: str, scale: float = 1.0) -> bool:
    """True, with a warning, when decoding this image would exceed the ceiling."""
    ceiling = max_decoded_bytes()
    size = decoded_image_bytes(xobj)
    if ceiling is None or size is None or size * scale <= ceiling:
        return False
    logger.warning(
        "%s: skipping an image that would need %d MB decoded (limit %d MB; "
        "raise PDFTL_MAX_DECODED_MB to process it)",
        what,
        round(size * scale / MB),
        round(ceiling / MB),
    )
    return True
