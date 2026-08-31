# tests/utils/test_asyncio.py

import asyncio
import threading

import pytest

from pdftl.utils.async_helpers import run_sync


def test_run_sync_without_active_event_loop():
    def sync_func():
        return asyncio.run(asyncio.sleep(0, result="validated"))

    assert run_sync(sync_func) == "validated"


def test_run_sync_active_event_loop():
    async def _async_runner():
        def sync_func():
            return asyncio.run(asyncio.sleep(0, result="validated"))

        return run_sync(sync_func)

    result = asyncio.run(_async_runner())

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
        asyncio.run(_async_runner())


def test_run_sync_active_event_loop_uses_different_thread():
    async def _async_runner():
        caller_thread = threading.current_thread()

        def sync_func():
            return threading.current_thread() is caller_thread

        return run_sync(sync_func)

    result = asyncio.run(_async_runner())

    assert result is False
