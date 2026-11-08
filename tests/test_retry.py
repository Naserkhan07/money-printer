import time

import pytest

from shorts_bot.retry import with_retry


def test_succeeds_first_try_without_sleeping() -> None:
    calls = []

    @with_retry(max_attempts=3, delay_seconds=5, jitter=False)
    def ok() -> str:
        calls.append("call")
        return "fine"

    assert ok() == "fine"
    assert calls == ["call"]


def test_retries_until_success_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    attempts = {"count": 0}

    @with_retry(max_attempts=3, delay_seconds=1.0, backoff_factor=2.0, jitter=False)
    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary")
        return "recovered"

    assert flaky() == "recovered"
    assert attempts["count"] == 3
    assert sleeps == [1.0, 2.0]


def test_raises_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    @with_retry(max_attempts=2, delay_seconds=0.1, jitter=False)
    def always_fails() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        always_fails()


def test_only_retries_configured_exceptions() -> None:
    @with_retry(max_attempts=3, delay_seconds=0.1, jitter=False, retriable_exceptions=(KeyError,))
    def wrong_error() -> None:
        raise ValueError("not retriable")

    with pytest.raises(ValueError):
        wrong_error()


def test_rejects_invalid_max_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        with_retry(max_attempts=0)
