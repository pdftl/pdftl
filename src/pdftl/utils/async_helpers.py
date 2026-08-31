# src/pdftl/utils/asyncio.py

import logging

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def run_sync(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous callable safely from sync or async context.

    If no event loop is running in the current thread, ``func`` is called
    directly. If an event loop is running, ``func`` is called in a temporary
    worker thread, allowing synchronous APIs that use ``asyncio.run()`` to
    work safely.

    The async-context path blocks the calling thread while waiting for the
    synchronous operation to complete, so this helper is intended for
    occasional blocking operations rather than high-frequency calls.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(*args, **kwargs)

    logger.debug(
        "Running %s in a worker thread because an asyncio event loop is active",
        getattr(func, "__qualname__", repr(func)),
    )

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(func, *args, **kwargs).result()
