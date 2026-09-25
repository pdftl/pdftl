# tests/utils/test_asyncio.py

import asyncio
import threading

import pytest

from pdftl.utils.async_helpers import run_sync


def _in_fresh_thread(func):
    """Call func in a new thread, which is guaranteed to have no running
    event loop regardless of state leaked into the test worker's main thread."""
    outcome = {}

    def target():
        try:
            outcome["result"] = func()
        except BaseException as exc:  # noqa: BLE001  # codeql[py/catch-base-exception]
            outcome["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def test_run_sync_without_active_event_loop():
    def sync_func():
        return asyncio.run(asyncio.sleep(0, result="validated"))

    assert _in_fresh_thread(lambda: run_sync(sync_func)) == "validated"


def test_run_sync_without_active_event_loop_calls_in_same_thread():
    def check():
        caller_thread = threading.current_thread()
        return run_sync(lambda: threading.current_thread() is caller_thread)

    assert _in_fresh_thread(check) is True


def test_run_sync_active_event_loop():
    async def _async_runner():
        def sync_func():
            return asyncio.run(asyncio.sleep(0, result="validated"))

        return run_sync(sync_func)

    result = _in_fresh_thread(lambda: asyncio.run(_async_runner()))

    assert result == "validated"


def test_run_sync_forwards_args_and_kwargs():
    def sync_func(a, b, *, c):
        return a + b + c

    assert run_sync(sync_func, 1, 2, c=3) == 6


def test_run_sync_propagates_exception():
    class ExpectedError(Exception):
        pass

    def sync_func():
        raise ExpectedError("boom")

    with pytest.raises(ExpectedError, match="boom"):
        run_sync(sync_func)


def test_run_sync_active_event_loop_propagates_exception():
    class ExpectedError(Exception):
        pass

    async def _async_runner():
        def sync_func():
            raise ExpectedError("boom")

        return run_sync(sync_func)

    with pytest.raises(ExpectedError, match="boom"):
        _in_fresh_thread(lambda: asyncio.run(_async_runner()))


def test_run_sync_active_event_loop_uses_different_thread():
    async def _async_runner():
        caller_thread = threading.current_thread()

        def sync_func():
            return threading.current_thread() is caller_thread

        return run_sync(sync_func)

    result = _in_fresh_thread(lambda: asyncio.run(_async_runner()))

    assert result is False
