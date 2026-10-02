# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_viewers.py

"""Tests for viewers: finding the OS's PDF viewers and launching one."""

import sys
import time
from pathlib import Path

import pytest

from pdftl.gui import viewers
from pdftl.gui.viewers import FILE, SYSTEM, Viewer


def test_a_viewer_puts_the_path_where_file_stands():
    v = Viewer("V", ("prog", "--flag", FILE))
    path = Path("x") / "a b.pdf"
    assert v.argv(path) == ["prog", "--flag", str(path)]
    assert not v.is_system
    assert SYSTEM.is_system and SYSTEM.name == "System viewer"


def test_program_viewer_runs_the_program_and_opens_mac_bundles_with_open():
    assert viewers.program_viewer("/usr/bin/zathura", macos=False) == Viewer(
        "zathura", ("/usr/bin/zathura", FILE)
    )
    assert viewers.program_viewer("/Applications/Skim.app/", macos=True) == Viewer(
        "Skim", ("open", "-a", "/Applications/Skim.app/", FILE)
    )
    assert (
        viewers.program_viewer("/opt/bin/qpdfview", macos=True).command[0] == "/opt/bin/qpdfview"
    )


def test_launch_starts_the_program_on_the_file(tmp_path):
    marker = tmp_path / "opened.txt"
    script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])"
    v = Viewer("py", (sys.executable, "-c", script, str(marker), FILE))
    assert viewers.launch(v, tmp_path / "doc.pdf") is True
    deadline = time.monotonic() + 15
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text() == str(tmp_path / "doc.pdf")


def test_launch_of_a_missing_program_fails(tmp_path):
    assert viewers.launch(Viewer("x", (str(tmp_path / "nope"), FILE)), tmp_path / "a.pdf") is False


# XDG


def _desktop(base: Path, name: str, body: str) -> Path:
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _entry(name, exec_line, mime="application/pdf;", extra=""):
    return f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\nMimeType={mime}\n{extra}"


def test_xdg_application_dirs_from_the_environment_and_defaults():
    env = {"XDG_DATA_HOME": "/h", "XDG_DATA_DIRS": "/a:/b"}
    assert viewers.xdg_application_dirs(env) == [
        Path("/h/applications"),
        Path("/a/applications"),
        Path("/b/applications"),
    ]
    assert viewers.xdg_application_dirs({}) == [
        Path.home() / ".local/share/applications",
        Path("/usr/local/share/applications"),
        Path("/usr/share/applications"),
    ]


def test_xdg_viewers_keep_pdf_openers_and_let_an_earlier_dir_win(tmp_path):
    user, system = tmp_path / "user", tmp_path / "system"
    _desktop(user, "evince.desktop", _entry("My Evince", "evince --mine %U"))
    _desktop(system, "evince.desktop", _entry("Evince", "evince %U"))
    _desktop(system, "kde4/okular.desktop", _entry("Okular", "okular %f"))
    _desktop(user, "kde4-okular.desktop", _entry("Hidden Okular", "x", extra="Hidden=true"))
    _desktop(system, "text.desktop", _entry("Editor", "gedit %F", mime="text/plain;"))
    _desktop(system, "nodisplay.desktop", _entry("N", "n %f", extra="NoDisplay=true"))
    _desktop(system, "link.desktop", _entry("L", "l %f").replace("Application", "Link"))
    _desktop(system, "noexec.desktop", _entry("E", ""))
    _desktop(system, "nosection.desktop", "[Other]\nName=x\n")
    _desktop(system, "junk.desktop", "not an ini file")
    (system / "binary.desktop").write_bytes(b"[Desktop Entry]\nName=\xff\n")
    _desktop(system, "noname.desktop", "[Desktop Entry]\nExec=mupdf\nMimeType=application/pdf\n")
    (system / "a-dir.desktop").mkdir()
    found = viewers.xdg_viewers([user, tmp_path / "missing", system])
    assert sorted(found, key=lambda v: v.name) == [
        Viewer("My Evince", ("evince", "--mine", FILE)),
        Viewer("noname", ("mupdf", FILE)),
    ]


@pytest.mark.parametrize(
    ("line", "argv"),
    [
        ("okular %U", ("okular", FILE)),
        ("app --icon %i %f", ("app", "--icon", FILE)),
        ("'my viewer' --level=100%% %F", ("my viewer", "--level=100%", FILE)),
        ("plain", ("plain", FILE)),
        ("broken 'quote", ()),
        ("", ()),
    ],
)
def test_desktop_exec_argv(line, argv):
    assert viewers.desktop_exec_argv(line) == argv


# Windows


class _Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Registry:
    """The few winreg calls `windows_viewers` makes, over a dict of keys."""

    HKEY_CLASSES_ROOT = "HKCR"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, keys):
        self.keys = keys

    def OpenKey(self, root, path):
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return _Key(self.keys[(root, path)])

    def QueryInfoKey(self, key):
        return (0, len(key.values), 0)

    def EnumValue(self, key, i):
        name = list(key.values)[i]
        return (name, key.values[name], 1)

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return (key.values[name], 1)


_OPEN_WITH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\OpenWithList"
_SUMATRA = r'"C:\Tools\SumatraPDF.exe" "%1"'


def _registry():
    return _Registry(
        {
            ("HKCR", r".pdf\OpenWithProgids"): {"Acro.Document": "", "NoCommand": "", "Bare": ""},
            ("HKCR", r"Acro.Document\shell\open\command"): {"": r'"C:\Acro\Acrobat.exe" "%1"'},
            ("HKCR", r"Acro.Document\Application"): {"ApplicationName": "Adobe Acrobat"},
            ("HKCR", r"Bare\shell\open\command"): {"": r"C:\bare.exe %1"},
            ("HKCR", "Bare"): {"": 7},
            ("HKCR", "NoCommand"): {"": "Nothing"},
            ("HKCU", _OPEN_WITH): {
                "a": "SumatraPDF.exe",
                "b": "",
                "c": "gone.exe",
                "MRUList": "abc",
            },
            ("HKCR", r"Applications\SumatraPDF.exe\shell\open\command"): {"": _SUMATRA},
            ("HKCR", r"Applications\SumatraPDF.exe"): {"FriendlyAppName": "Sumatra PDF"},
        }
    )


def test_windows_viewers_come_from_progids_and_the_open_with_list():
    assert viewers.windows_viewers(_registry()) == [
        Viewer("Adobe Acrobat", (r"C:\Acro\Acrobat.exe", FILE)),
        Viewer("Bare", (r"C:\bare.exe", FILE)),
        Viewer("Sumatra PDF", (r"C:\Tools\SumatraPDF.exe", FILE)),
    ]


def test_windows_viewers_with_nothing_registered():
    assert viewers.windows_viewers(_Registry({})) == []


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        (r'"%ProgramFiles%\V\v.exe" "%1"', (r"C:\PF\V\v.exe", FILE)),
        (r"v.exe /view %L %*", ("v.exe", "/view", FILE)),
        (r"v.exe %UNSET% %l", ("v.exe", "%UNSET%", FILE)),
        (r"v.exe", ("v.exe", FILE)),
        ("", ()),
    ],
)
def test_windows_command_argv(command, argv):
    assert viewers.windows_command_argv(command, {"ProgramFiles": r"C:\PF"}) == argv


def test_windows_command_argv_uses_the_process_environment(monkeypatch):
    monkeypatch.setenv("PDFTL_TEST_DIR", "D")
    assert viewers.windows_command_argv(r"%PDFTL_TEST_DIR%\v.exe")[0] == r"D\v.exe"


# discover


def test_discover_on_windows_reads_the_registry(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _registry())
    assert [v.name for v in viewers.discover("win32")] == ["Adobe Acrobat", "Bare", "Sumatra PDF"]


def test_discover_on_macos_lists_the_launch_services_apps(monkeypatch):
    apps = ["/Applications/Skim.app", "/System/Applications/Preview.app", "/Applications/Skim.app"]
    monkeypatch.setattr(viewers, "_mac_app_paths", lambda: apps)
    assert viewers.discover("darwin") == [
        Viewer("Preview", ("open", "-a", "/System/Applications/Preview.app", FILE)),
        Viewer("Skim", ("open", "-a", "/Applications/Skim.app", FILE)),
    ]


def test_discover_elsewhere_reads_desktop_entries_sorted_and_deduplicated(monkeypatch, tmp_path):
    _desktop(tmp_path / "h/applications", "z.desktop", _entry("zathura", "zathura %f"))
    _desktop(tmp_path / "d/applications", "a.desktop", _entry("Atril", "atril %U"))
    _desktop(tmp_path / "d/applications", "a2.desktop", _entry("Atril", "atril %U"))
    dirs = [tmp_path / "h/applications", tmp_path / "d/applications"]
    monkeypatch.setattr(viewers, "xdg_application_dirs", lambda: dirs)
    assert viewers.discover("linux") == [
        Viewer("Atril", ("atril", FILE)),
        Viewer("zathura", ("zathura", FILE)),
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="LaunchServices is macOS only")
def test_launch_services_offers_preview_for_pdfs():
    paths = viewers._mac_app_paths()
    assert any(p.rstrip("/").endswith("/Preview.app") for p in paths)
    assert all(Path(p).exists() for p in paths)
    assert all(v.command[:2] == ("open", "-a") for v in viewers.discover())


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows only")
def test_the_real_registry_gives_runnable_viewers():
    assert all(FILE in v.command and v.name for v in viewers.discover())
