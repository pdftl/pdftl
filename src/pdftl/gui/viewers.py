# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/viewers.py

"""PDF viewers: the ones the OS knows about, and launching the chosen one.

Qt-free. A viewer is a name and an argv in which `FILE` stands for the
PDF; an empty argv means the system viewer, which the caller opens.
"""

from __future__ import annotations

import configparser
import os
import re
import shlex
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

FILE = "{file}"
SYSTEM_NAME = "System viewer"
PDF_MIME = "application/pdf"
_FILE_CODES = {"%f", "%F", "%u", "%U"}
_WINDOWS_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_WINDOWS_ENV = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_OPEN_WITH_LIST = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithList"


@dataclass(frozen=True)
class Viewer:
    name: str
    command: tuple[str, ...] = ()

    @property
    def is_system(self) -> bool:
        return not self.command

    def argv(self, path: Path) -> list[str]:
        return [str(path) if arg == FILE else arg for arg in self.command]


SYSTEM = Viewer(SYSTEM_NAME)


def program_viewer(program: str, macos: bool = sys.platform == "darwin") -> Viewer:
    """A viewer that runs `program` on the file; a macOS app bundle is opened with `open`."""
    name = Path(program).stem or program
    if macos and program.rstrip("/").endswith(".app"):
        return Viewer(name, ("open", "-a", program, FILE))
    return Viewer(name, (program, FILE))


def launch(viewer: Viewer, path: Path) -> bool:
    """Start `viewer` on `path`, detached; False if it could not be started."""
    detach: dict = {"start_new_session": True}
    if os.name == "nt":  # pragma: no cover -- Windows
        detach = {"creationflags": subprocess.DETACHED_PROCESS}
    try:
        subprocess.Popen(  # nosec B603 # nosemgrep -- argv list, no shell
            viewer.argv(path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detach,
        )
    except OSError:
        return False
    return True


def discover(platform: str = sys.platform) -> list[Viewer]:
    """The PDF viewers the OS lists, by name, without duplicates."""
    if platform == "win32":
        import winreg

        found = windows_viewers(winreg)
    elif platform == "darwin":
        found = [program_viewer(p, macos=True) for p in _mac_app_paths()]
    else:
        found = xdg_viewers(xdg_application_dirs())
    unique = {v.command: v for v in reversed(found)}
    return sorted(unique.values(), key=lambda v: v.name.casefold())


# XDG desktop entries (Linux, BSD)


def xdg_application_dirs(env: dict[str, str] | None = None) -> list[Path]:
    env = os.environ if env is None else env
    home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(d) / "applications" for d in [home, *dirs.split(":")] if d]


def xdg_viewers(dirs: list[Path]) -> list[Viewer]:
    """Viewers from the desktop entries that open PDFs; an earlier dir's entry wins."""
    seen: dict[str, Viewer | None] = {}
    for base in dirs:
        for entry in sorted(base.rglob("*.desktop")) if base.is_dir() else ():
            desktop_id = str(entry.relative_to(base)).replace(os.sep, "-")
            if desktop_id not in seen:
                seen[desktop_id] = _desktop_viewer(entry)
    return [v for v in seen.values() if v is not None]


def _desktop_viewer(path: Path) -> Viewer | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (configparser.Error, UnicodeDecodeError, KeyError):
        return None
    shown = entry.get("NoDisplay") != "true" and entry.get("Hidden") != "true"
    pdf = PDF_MIME in entry.get("MimeType", "").split(";")
    if not (shown and pdf and entry.get("Type", "Application") == "Application"):
        return None
    command = desktop_exec_argv(entry.get("Exec", ""))
    return Viewer(entry.get("Name", path.stem), command) if command else None


def desktop_exec_argv(exec_line: str) -> tuple[str, ...]:
    """The argv of a desktop entry's Exec line, with its file field code as `FILE`."""
    try:
        words = shlex.split(exec_line)
    except ValueError:
        return ()
    argv = []
    for word in words:
        if word in _FILE_CODES:
            argv.append(FILE)
        elif not re.fullmatch(r"%[a-zA-Z]", word):
            argv.append(word.replace("%%", "%"))
    if argv and FILE not in argv:
        argv.append(FILE)
    return tuple(argv)


# Windows registry


def windows_viewers(reg) -> list[Viewer]:
    """Viewers registered for .pdf; `reg` is `winreg` or a stand-in for it."""
    found = []
    for progid in _value_names(reg, reg.HKEY_CLASSES_ROOT, r".pdf\OpenWithProgids"):
        command = _default(reg, reg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command")
        name = _value(reg, reg.HKEY_CLASSES_ROOT, rf"{progid}\Application", "ApplicationName")
        name = name or _default(reg, reg.HKEY_CLASSES_ROOT, progid) or progid
        found.append((name, command))
    for key in _value_names(reg, reg.HKEY_CURRENT_USER, _OPEN_WITH_LIST):
        exe = _value(reg, reg.HKEY_CURRENT_USER, _OPEN_WITH_LIST, key)
        if key == "MRUList" or not exe:
            continue
        app = rf"Applications\{exe}"
        command = _default(reg, reg.HKEY_CLASSES_ROOT, rf"{app}\shell\open\command")
        name = _value(reg, reg.HKEY_CLASSES_ROOT, app, "FriendlyAppName") or Path(exe).stem
        found.append((name, command))
    return [Viewer(name, windows_command_argv(cmd)) for name, cmd in found if cmd]


def windows_command_argv(command: str, env: dict[str, str] | None = None) -> tuple[str, ...]:
    """The argv of a shell\\open\\command string, with `%1` or `%L` as `FILE`."""
    env = os.environ if env is None else env
    expanded = _WINDOWS_ENV.sub(lambda m: env.get(m.group(1), m.group(0)), command)
    argv = []
    for quoted, bare in _WINDOWS_TOKEN.findall(expanded):
        word = quoted or bare
        if word in ("%1", "%L", "%l"):
            argv.append(FILE)
        elif word != "%*":
            argv.append(word)
    if argv and FILE not in argv:
        argv.append(FILE)
    return tuple(argv)


def _value_names(reg, root, path: str) -> list[str]:
    try:
        with reg.OpenKey(root, path) as key:
            count = reg.QueryInfoKey(key)[1]
            return [reg.EnumValue(key, i)[0] for i in range(count)]
    except OSError:
        return []


def _value(reg, root, path: str, name: str) -> str:
    try:
        with reg.OpenKey(root, path) as key:
            value = reg.QueryValueEx(key, name)[0]
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _default(reg, root, path: str) -> str:
    return _value(reg, root, path, "")


# macOS LaunchServices


def _mac_app_paths() -> list[str]:  # pragma: no cover -- macOS
    """The app bundles LaunchServices offers for opening a PDF."""
    import ctypes
    import ctypes.util

    names = [ctypes.util.find_library(n) for n in ("CoreFoundation", "CoreServices")]
    if not all(names):
        return []
    cf, ls = (ctypes.CDLL(name) for name in names)
    p = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.restype = p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
        p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_bool,
    ]
    ls.LSCopyApplicationURLsForURL.restype = p
    ls.LSCopyApplicationURLsForURL.argtypes = [p, ctypes.c_uint32]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [p]
    cf.CFArrayGetValueAtIndex.restype = p
    cf.CFArrayGetValueAtIndex.argtypes = [p, ctypes.c_long]
    cf.CFURLGetFileSystemRepresentation.restype = ctypes.c_bool
    cf.CFURLGetFileSystemRepresentation.argtypes = [
        p,
        ctypes.c_bool,
        ctypes.c_char_p,
        ctypes.c_long,
    ]
    cf.CFRelease.argtypes = [p]
    with tempfile.NamedTemporaryFile(suffix=".pdf") as sample:
        name = sample.name.encode()
        url = cf.CFURLCreateFromFileSystemRepresentation(None, name, len(name), False)
        apps = ls.LSCopyApplicationURLsForURL(url, 0xFFFFFFFF)
        cf.CFRelease(url)
    if not apps:
        return []
    paths = []
    buffer = ctypes.create_string_buffer(4096)
    for i in range(cf.CFArrayGetCount(apps)):
        if cf.CFURLGetFileSystemRepresentation(
            cf.CFArrayGetValueAtIndex(apps, i), True, buffer, 4096
        ):
            paths.append(buffer.value.decode())
    cf.CFRelease(apps)
    return paths
