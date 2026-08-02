"""Bounded retries for provider requests.

Providers that share an anonymous, public endpoint are asked to back off when
they are busy. The policy here is a value object and the pause is injected, so
the schedule can be asserted in a test without any real waiting.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from maxicrawler.providers.errors import ProviderRateLimitError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How often and how patiently a failed request is repeated.

    The schedule is exponential: the pause after the *n*-th failed attempt is
    ``initial_delay * multiplier ** (n - 1)``, capped at ``max_delay``.
    """

    max_attempts: int = 3
    initial_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        if self.initial_delay < 0:
            msg = "initial_delay must not be negative"
            raise ValueError(msg)
        if self.multiplier < 1:
            msg = "multiplier must be at least 1"
            raise ValueError(msg)
        if self.max_delay < self.initial_delay:
            msg = "max_delay must not be smaller than initial_delay"
            raise ValueError(msg)

    def delay_for(self, attempt: int) -> float:
        """Return the pause in seconds that follows the *attempt*-th failure.

        *attempt* is one-based, so ``delay_for(1)`` is the wait after the first
        failure.
        """
        if attempt < 1:
            msg = "attempt must be at least 1"
            raise ValueError(msg)
        return min(self.initial_delay * self.multiplier ** (attempt - 1), self.max_delay)


class Retrier:
    """Repeats an operation according to a :class:`RetryPolicy`.

    A provider that asks for a specific pause is obeyed: a
    :class:`ProviderRateLimitError` carrying ``retry_after`` overrides the
    computed delay, still bounded by :attr:`RetryPolicy.max_delay` so a hostile
    answer cannot stall the process.
    """

    def __init__(
        self,
        policy: RetryPolicy | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._policy = policy if policy is not None else RetryPolicy()
        self._sleep = sleep

    @property
    def policy(self) -> RetryPolicy:
        """Return the schedule this retrier follows."""
        return self._policy

    def call(
        self,
        operation: Callable[[], T],
        *,
        retry_on: tuple[type[Exception], ...],
    ) -> T:
        """Run *operation*, repeating it while it raises one of *retry_on*.

        The final failure is re-raised unchanged, so the caller sees the reason
        the operation actually gave up.
        """
        attempt = 1
        while True:
            try:
                return operation()
            except retry_on as error:
                if attempt >= self._policy.max_attempts:
                    raise
                self._sleep(self._delay_after(error, attempt))
                attempt += 1

    def _delay_after(self, error: Exception, attempt: int) -> float:
        """Return how long to wait after *error* on the *attempt*-th try."""
        delay = self._policy.delay_for(attempt)
        if isinstance(error, ProviderRateLimitError) and error.retry_after is not None:
            return min(error.retry_after, self._policy.max_delay)
        return delay
