"""Retry helpers with exponential backoff and jitter.

Ported from the MIT-licensed social-media-scheduler by HillzKillzIt
(https://github.com/HillzKillzIt/social-media-scheduler) and adapted to this
project's logging conventions. See NOTICE.md for attribution.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    delay_seconds: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retriable_exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    Decorator that retries a function on failure with exponential backoff.

    Args:
        max_attempts: Total number of attempts (including the first).
        delay_seconds: Initial delay between retries in seconds.
        backoff_factor: Multiplier applied to the delay after each failure.
        jitter: Add random jitter (±20%) to avoid thundering-herd problems.
        retriable_exceptions: Exception types that should trigger a retry.

    Usage:
        @with_retry(max_attempts=3, delay_seconds=5)
        def call_api(...):
            ...
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = float(delay_seconds)
            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 1:
                        logger.info(
                            "[%s] Succeeded on attempt %d/%d",
                            func.__name__, attempt, max_attempts,
                        )
                    return result
                except retriable_exceptions as exc:  # noqa: PERF203
                    last_exception = exc
                    if attempt == max_attempts:
                        logger.error(
                            "[%s] Failed after %d attempts. Last error: %s",
                            func.__name__, max_attempts, exc,
                        )
                        raise

                    sleep_time = current_delay
                    if jitter:
                        sleep_time *= random.uniform(0.8, 1.2)

                    logger.warning(
                        "[%s] Attempt %d/%d failed: %s. Retrying in %.1fs...",
                        func.__name__, attempt, max_attempts, exc, sleep_time,
                    )
                    time.sleep(sleep_time)
                    current_delay *= backoff_factor

            raise last_exception  # pragma: no cover - defensive, unreachable

        return wrapper

    return decorator
