"""Tests for the retry policy and the retrier."""

import pytest

from maxicrawler.providers import (
    ProviderRateLimitError,
    ProviderTransportError,
    Retrier,
    RetryPolicy,
)


class RecordingSleep:
    """Captures the pauses a retrier asks for without waiting for them."""

    def __init__(self) -> None:
        self.pauses: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.pauses.append(seconds)


class FailingOperation:
    """Fails a fixed number of times, then succeeds."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self._remaining = failures
        self._error = error if error is not None else ProviderTransportError("boom")
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self._remaining:
            self._remaining -= 1
            raise self._error
        return "done"


def test_policy_grows_the_delay_exponentially() -> None:
    policy = RetryPolicy(max_attempts=5, initial_delay=1.0, multiplier=2.0, max_delay=30.0)

    assert [policy.delay_for(attempt) for attempt in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


def test_policy_caps_the_delay() -> None:
    policy = RetryPolicy(max_attempts=10, initial_delay=1.0, multiplier=10.0, max_delay=5.0)

    assert policy.delay_for(3) == 5.0


def test_policy_rejects_an_attempt_below_one() -> None:
    with pytest.raises(ValueError, match="attempt must be at least 1"):
        RetryPolicy().delay_for(0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts must be at least 1"),
        ({"initial_delay": -1.0}, "initial_delay must not be negative"),
        ({"multiplier": 0.5}, "multiplier must be at least 1"),
        ({"initial_delay": 10.0, "max_delay": 1.0}, "max_delay must not be smaller"),
    ],
)
def test_policy_rejects_an_impossible_schedule(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_retrier_returns_the_result_without_retrying() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=0)

    result = Retrier(RetryPolicy(), sleep=sleep).call(operation, retry_on=(ProviderTransportError,))

    assert result == "done"
    assert operation.calls == 1
    assert sleep.pauses == []


def test_retrier_repeats_until_the_operation_succeeds() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=2)
    policy = RetryPolicy(max_attempts=3, initial_delay=1.0, multiplier=2.0)

    result = Retrier(policy, sleep=sleep).call(operation, retry_on=(ProviderTransportError,))

    assert result == "done"
    assert operation.calls == 3
    assert sleep.pauses == [1.0, 2.0]


def test_retrier_reraises_the_final_failure() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=5)
    policy = RetryPolicy(max_attempts=3, initial_delay=0.5)

    with pytest.raises(ProviderTransportError, match="boom"):
        Retrier(policy, sleep=sleep).call(operation, retry_on=(ProviderTransportError,))

    assert operation.calls == 3
    assert sleep.pauses == [0.5, 1.0]


def test_retrier_does_not_retry_an_unlisted_error() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=1, error=ValueError("nope"))

    with pytest.raises(ValueError, match="nope"):
        Retrier(RetryPolicy(), sleep=sleep).call(operation, retry_on=(ProviderTransportError,))

    assert operation.calls == 1
    assert sleep.pauses == []


def test_retrier_obeys_a_requested_pause() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(
        failures=1, error=ProviderRateLimitError("slow down", retry_after=7.0)
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=1.0)

    Retrier(policy, sleep=sleep).call(operation, retry_on=(ProviderRateLimitError,))

    assert sleep.pauses == [7.0]


def test_retrier_caps_a_requested_pause() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(
        failures=1, error=ProviderRateLimitError("slow down", retry_after=9000.0)
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=1.0, max_delay=30.0)

    Retrier(policy, sleep=sleep).call(operation, retry_on=(ProviderRateLimitError,))

    assert sleep.pauses == [30.0]


def test_retrier_falls_back_to_the_schedule_without_a_requested_pause() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=1, error=ProviderRateLimitError("slow down"))
    policy = RetryPolicy(max_attempts=2, initial_delay=3.0)

    Retrier(policy, sleep=sleep).call(operation, retry_on=(ProviderRateLimitError,))

    assert sleep.pauses == [3.0]


def test_retrier_uses_a_default_policy() -> None:
    assert Retrier().policy == RetryPolicy()


def test_retrier_honours_a_single_attempt_policy() -> None:
    sleep = RecordingSleep()
    operation = FailingOperation(failures=1)

    with pytest.raises(ProviderTransportError):
        Retrier(RetryPolicy(max_attempts=1), sleep=sleep).call(
            operation, retry_on=(ProviderTransportError,)
        )

    assert operation.calls == 1
    assert sleep.pauses == []
