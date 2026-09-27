# tests/utils/test_system_memory.py
#
# Each probe is fed a platform's own output format; sizes are worked out
# by hand from the image dictionaries.

import logging
import sys
import types

import pikepdf
import pytest

import pdftl.utils.system_memory as sm
from pdftl.utils.system_memory import MB

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                            500000.
Pages inactive:                            2000.
Pages speculative:                          300.
Pages throttled:                              0.
Pages wired down:                        100000.
Pages purgeable:                             40.
"""


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for key in ("PDFTL_IMAGE_MEMORY_MB", "PDFTL_MAX_DECODED_MB"):
        monkeypatch.delenv(key, raising=False)


def _no_probes(monkeypatch, keep=()):
    for name in (
        "_from_psutil",
        "_from_meminfo",
        "_from_windows",
        "_from_sysconf",
        "_from_vm_stat",
        "_cgroup_headroom",
    ):
        if name not in keep:
            monkeypatch.setattr(sm, name, lambda: None)


# --- available memory ---


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux totals")
def test_real_reading_is_below_physical_memory():
    import os

    total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    assert 0 < sm.available_memory_bytes() <= total


def test_meminfo(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  8000000 kB\nMemFree:  100 kB\nMemAvailable:  4000000 kB\n")
    monkeypatch.setattr(sm, "_MEMINFO", str(meminfo))
    assert sm._from_meminfo() == 4000000 * 1024
    meminfo.write_text("MemTotal:  8000000 kB\n")  # kernels before 3.14
    assert sm._from_meminfo() is None
    monkeypatch.setattr(sm, "_MEMINFO", str(tmp_path / "missing"))
    assert sm._from_meminfo() is None


@pytest.mark.parametrize("line", ["MemAvailable: lots kB\n", "MemAvailable:\n"])
def test_malformed_meminfo(tmp_path, monkeypatch, line):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(line)
    monkeypatch.setattr(sm, "_MEMINFO", str(meminfo))
    assert sm._from_meminfo() is None


def test_psutil(monkeypatch):
    fake = types.SimpleNamespace(virtual_memory=lambda: types.SimpleNamespace(available=777))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert sm._from_psutil() == 777
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert sm._from_psutil() is None

    def no_proc():
        raise FileNotFoundError("/proc/meminfo")

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(virtual_memory=no_proc))
    assert sm._from_psutil() is None


def test_windows(monkeypatch):
    import ctypes

    def global_memory_status_ex(ref):
        assert ref._obj.dwLength == ctypes.sizeof(ref._obj)
        ref._obj.ullAvailPhys = 3 * 1024**3
        return 1

    kernel32 = types.SimpleNamespace(GlobalMemoryStatusEx=global_memory_status_ex)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setattr(sm.sys, "platform", "win32")
    assert sm._from_windows() == 3 * 1024**3
    kernel32.GlobalMemoryStatusEx = lambda ref: 0
    assert sm._from_windows() is None
    monkeypatch.setattr(sm.sys, "platform", "linux")
    assert sm._from_windows() is None


def test_sysconf(monkeypatch):
    values = {"SC_AVPHYS_PAGES": 1000, "SC_PAGE_SIZE": 4096}
    monkeypatch.setattr(sm.os, "sysconf_names", values, raising=False)
    monkeypatch.setattr(sm.os, "sysconf", values.__getitem__, raising=False)
    assert sm._from_sysconf() == 4096000
    monkeypatch.setattr(sm.os, "sysconf_names", {"SC_PAGE_SIZE": 4}, raising=False)  # macOS
    assert sm._from_sysconf() is None

    def einval(name):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(sm.os, "sysconf_names", values, raising=False)
    monkeypatch.setattr(sm.os, "sysconf", einval, raising=False)
    assert sm._from_sysconf() is None


def test_vm_stat(monkeypatch):
    assert sm.parse_vm_stat(VM_STAT) == (10000 + 2000 + 300 + 40) * 16384
    assert sm.parse_vm_stat("Pages free: 5.") is None
    assert sm.parse_vm_stat("(page size of 4096 bytes)\nPages active: 5.") is None

    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=VM_STAT)

    monkeypatch.setattr(sm.subprocess, "run", run)
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    assert sm._from_vm_stat() == (10000 + 2000 + 300 + 40) * 16384
    monkeypatch.setattr(sm.sys, "platform", "linux")
    assert sm._from_vm_stat() is None
    assert calls == [["vm_stat"]]


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("vm_stat"),
        sm.subprocess.CalledProcessError(1, ["vm_stat"]),
        sm.subprocess.TimeoutExpired(["vm_stat"], 2),
    ],
)
def test_failing_vm_stat(monkeypatch, error):
    def run(cmd, **kwargs):
        raise error

    monkeypatch.setattr(sm.subprocess, "run", run)
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    assert sm._from_vm_stat() is None


def test_first_working_probe_wins(monkeypatch):
    _no_probes(monkeypatch)
    monkeypatch.setattr(sm, "_from_sysconf", lambda: 42)
    monkeypatch.setattr(sm, "_from_vm_stat", lambda: 99)
    assert sm.available_memory_bytes() == 42


def test_cgroup_limit_caps_the_host_figure(monkeypatch):
    _no_probes(monkeypatch)
    monkeypatch.setattr(sm, "_from_meminfo", lambda: 8000 * MB)
    monkeypatch.setattr(sm, "_cgroup_headroom", lambda: 300 * MB)
    assert sm.available_memory_bytes() == 300 * MB
    monkeypatch.setattr(sm, "_from_meminfo", lambda: None)
    assert sm.available_memory_bytes() == 300 * MB


def _cgroup_tree(tmp_path, monkeypatch, self_line, files):
    (tmp_path / "self").write_text(self_line)
    monkeypatch.setattr(sm, "_SELF_CGROUP", str(tmp_path / "self"))
    root = tmp_path / "cg"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    monkeypatch.setattr(sm, "_CGROUP_ROOT", str(root))


def test_cgroup_v2_takes_the_tightest_ancestor(tmp_path, monkeypatch):
    _cgroup_tree(
        tmp_path,
        monkeypatch,
        "0::/user.slice/job.scope\n",
        {
            "user.slice/job.scope/memory.max": "max\n",  # no limit of its own
            "user.slice/job.scope/memory.current": "100\n",
            "user.slice/memory.max": "1000\n",
            "user.slice/memory.current": "400\n",
            "memory.max": "5000\n",
            "memory.current": "1000\n",
        },
    )
    assert sm._cgroup_headroom() == 600


def test_cgroup_over_its_limit_has_no_headroom(tmp_path, monkeypatch):
    _cgroup_tree(
        tmp_path,
        monkeypatch,
        "0::/a\n",
        {"a/memory.max": "1000", "a/memory.current": "1200"},
    )
    assert sm._cgroup_headroom() == 0


def test_cgroup_v1(tmp_path, monkeypatch):
    unlimited = str((1 << 63) - 4096)
    files = {"memory/memory.limit_in_bytes": "2000", "memory/memory.usage_in_bytes": "500"}
    _cgroup_tree(tmp_path, monkeypatch, "12:memory:/docker/abc\n", files)
    assert sm._cgroup_headroom() == 1500
    files["memory/memory.limit_in_bytes"] = unlimited
    _cgroup_tree(tmp_path, monkeypatch, "12:memory:/\n", files)
    assert sm._cgroup_headroom() is None


def test_no_cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_SELF_CGROUP", str(tmp_path / "missing"))
    assert sm._cgroup_headroom() is None
    _cgroup_tree(tmp_path, monkeypatch, "0::/\n", {})  # no memory controller files
    assert sm._cgroup_headroom() is None


def test_unknown_when_every_probe_fails(monkeypatch):
    _no_probes(monkeypatch)
    assert sm.available_memory_bytes() is None


# --- budget and ceiling ---


def test_budget_is_half_of_available_within_limits(monkeypatch):
    for available, expected in [
        (2000 * MB, 1000 * MB),
        (100 * MB, sm.MIN_BUDGET_MB * MB),
        (64000 * MB, sm.MAX_BUDGET_MB * MB),
        (None, sm.DEFAULT_BUDGET_MB * MB),
    ]:
        monkeypatch.setattr(sm, "available_memory_bytes", lambda a=available: a)
        assert sm.image_job_budget_bytes() == expected


def test_budget_override(monkeypatch, caplog):
    monkeypatch.setattr(sm, "available_memory_bytes", lambda: None)
    monkeypatch.setenv("PDFTL_IMAGE_MEMORY_MB", "64")
    assert sm.image_job_budget_bytes() == 64 * MB
    monkeypatch.setenv("PDFTL_IMAGE_MEMORY_MB", "0")
    assert sm.image_job_budget_bytes() == sm.DEFAULT_BUDGET_MB * MB
    monkeypatch.setenv("PDFTL_IMAGE_MEMORY_MB", "lots")
    with caplog.at_level(logging.WARNING):
        assert sm.image_job_budget_bytes() == sm.DEFAULT_BUDGET_MB * MB
    assert "PDFTL_IMAGE_MEMORY_MB" in caplog.text


def test_ceiling(monkeypatch):
    assert sm.max_decoded_bytes() == sm.DEFAULT_MAX_DECODED_MB * MB
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", "10.5")
    assert sm.max_decoded_bytes() == int(10.5 * MB)
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", "0")
    assert sm.max_decoded_bytes() is None


# --- decoded sizes ---


def _img(pdf, **entries):
    stream = pdf.make_stream(b"", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image)
    for key, value in entries.items():
        stream["/" + key] = value
    return stream


@pytest.mark.parametrize(
    "entries,expected",
    [
        ({"ColorSpace": pikepdf.Name.DeviceRGB, "BitsPerComponent": 8}, 100 * 50 * 3),
        ({"ColorSpace": pikepdf.Name.DeviceGray, "BitsPerComponent": 1}, 100 * 50),
        ({"ColorSpace": pikepdf.Name.DeviceCMYK, "BitsPerComponent": 16}, 100 * 50 * 8),
        ({"ImageMask": True, "BitsPerComponent": 1}, 100 * 50),
        ({}, 100 * 50 * 3),  # no ColorSpace: RGB, 8 bits
        ({"ColorSpace": pikepdf.Name.Pattern}, 100 * 50 * sm.UNKNOWN_COMPONENTS),
        (
            {
                "ColorSpace": pikepdf.Array(
                    [pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 1, b"\0" * 6]
                )
            },
            100 * 50,
        ),
        (
            {
                "ColorSpace": pikepdf.Array(
                    [
                        pikepdf.Name.DeviceN,
                        pikepdf.Array([pikepdf.Name.C, pikepdf.Name.M]),
                        pikepdf.Name.DeviceCMYK,
                    ]
                )
            },
            100 * 50 * 2,
        ),
        ({"ColorSpace": pikepdf.Array([])}, 100 * 50 * sm.UNKNOWN_COMPONENTS),
    ],
)
def test_decoded_bytes(entries, expected):
    pdf = pikepdf.new()
    assert sm.decoded_image_bytes(_img(pdf, Width=100, Height=50, **entries)) == expected


def test_icc_components():
    pdf = pikepdf.new()
    icc = pdf.make_stream(b"", N=4)
    cs = pikepdf.Array([pikepdf.Name.ICCBased, icc])
    assert sm.decoded_image_bytes(_img(pdf, Width=10, Height=10, ColorSpace=cs)) == 400
    bad = pikepdf.Array([pikepdf.Name.ICCBased, 5])
    assert (
        sm.decoded_image_bytes(_img(pdf, Width=10, Height=10, ColorSpace=bad))
        == 100 * sm.UNKNOWN_COMPONENTS
    )


def test_soft_mask_counts():
    pdf = pikepdf.new()
    mask = _img(pdf, Width=100, Height=50, ColorSpace=pikepdf.Name.DeviceGray)
    img = _img(pdf, Width=100, Height=50, ColorSpace=pikepdf.Name.DeviceRGB, SMask=mask)
    assert sm.decoded_image_bytes(img) == 100 * 50 * 4


@pytest.mark.parametrize(
    "entries", [{}, {"Width": 0, "Height": 5}, {"Width": pikepdf.Name.X, "Height": 5}]
)
def test_unknowable_size(entries):
    assert sm.decoded_image_bytes(_img(pikepdf.new(), **entries)) is None
    assert sm.decoded_image_bytes(object()) is None


def test_too_large_to_decode(monkeypatch, caplog):
    pdf = pikepdf.new()
    img = _img(pdf, Width=1000, Height=1000, ColorSpace=pikepdf.Name.DeviceRGB)  # ~2.9 MB
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", "2")
    with caplog.at_level(logging.WARNING):
        assert sm.too_large_to_decode(img, "resample_images")
    assert "resample_images: skipping an image that would need 3 MB decoded" in caplog.text
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", "4")
    assert not sm.too_large_to_decode(img, "x")
    assert sm.too_large_to_decode(img, "x", scale=2)
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", "0")
    assert not sm.too_large_to_decode(img, "x", scale=100)
    assert not sm.too_large_to_decode(_img(pdf), "x")
