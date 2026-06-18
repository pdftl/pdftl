# tests/operations/helpers/test_image_processor.py

import io
from unittest.mock import MagicMock

import pikepdf

from pdftl.operations.helpers.image_processor import (
    ImageContext,
    ensure_thread_safe,
    get_orig_stream_size,
    run_parallel_image_job,
)


def test_ensure_thread_safe_logic():
    # Case 1: Already loaded (fp is None)
    img1 = MagicMock()
    img1.fp = None
    ensure_thread_safe(img1)
    img1.load.assert_not_called()

    # Case 2: Backed by thread-safe pure Python memory buffer
    img2 = MagicMock()
    img2.fp = io.BytesIO()
    ensure_thread_safe(img2)
    img2.load.assert_not_called()

    # Case 3: Backed by an unsafe live file descriptor (or C++ proxy stream)
    img3 = MagicMock()
    img3.fp = "some_unsafe_file_pointer"
    ensure_thread_safe(img3)
    img3.load.assert_called_once()


def test_get_orig_stream_size():
    # Null or invalid handling
    assert get_orig_stream_size(None) == 999_999_999

    stream_mock = MagicMock()

    # Successful size retrieval
    stream_mock.read_raw_bytes.return_value = b"12345"
    assert get_orig_stream_size(stream_mock) == 5

    # Exception handling (PdfError from Pikepdf binding)
    stream_mock.read_raw_bytes.side_effect = pikepdf.PdfError("Mock error")
    assert get_orig_stream_size(stream_mock) == 999_999_999


def test_run_parallel_image_job_success_and_exceptions():
    """Validates that the parallel processor cleanly handles successes and worker crashes."""

    # Dummy mock objects
    mock_images = [{"name": "img1"}, {"name": "img2"}, {"name": "img3"}]

    # 1. Prepare function generates a dummy tuple, but fails on img3
    def mock_prepare(img_dict, seen_set):
        if img_dict["name"] == "img3":
            return None  # Simulate an extraction skip or failure

        ctx = ImageContext(
            xobj=MagicMock(), smask_xobj=None, orig_size=100, img_dict=img_dict, page_num=1
        )
        return "dummy_payload", ctx

    # 2. Worker computes a result, but crashes on img2
    def mock_worker(payload):
        if payload == "dummy_payload":
            # Just some arbitrary state to force a failure condition via closure
            if not hasattr(mock_worker, "called_once"):
                mock_worker.called_once = True
                return "dummy_result"
            else:
                raise RuntimeError("Simulated thread crash for img2")

    # 3. Commit function always succeeds
    def mock_commit(ctx, result, payload):
        return True

    # Execute
    resample_count = run_parallel_image_job(
        images=mock_images,
        threads=2,
        prepare_func=mock_prepare,
        worker_func=mock_worker,
        commit_func=mock_commit,
    )

    # 3 images total:
    # - img1 succeeds
    # - img2 crashes in worker (caught by loop, doesn't crash app)
    # - img3 returns None from prepare (skips execution)
    assert resample_count == 1


from unittest.mock import patch


@patch("os.cpu_count", return_value=8)
def test_run_parallel_image_job_default_threads(mock_cpu_count):
    """Validates that missing or invalid thread counts fallback to system defaults."""

    # Passing threads=None or threads=0 should trigger the os.cpu_count() fallback
    run_parallel_image_job(
        images=[],
        threads=0,
        prepare_func=lambda img, seen: None,
        worker_func=lambda p: p,
        commit_func=lambda c, r, p: True,
    )
    mock_cpu_count.assert_called_once()
