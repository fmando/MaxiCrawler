"""Tests for per-host politeness.

No test here sleeps. The clock and the waiter are both injected, so what is
asserted is the *decision* to wait and for how long, which is the part worth
being sure about.
"""

import pytest

from maxicrawler.web import FetchedPage, PageFetcher
from maxicrawler.web.throttle import HostSchedule, ThrottledFetcher, host_key


class FakeTime:
    """A clock that only moves when somebody waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.waited: list[float] = []

    def clock(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waited.append(seconds)
        self.now += seconds

    def pass_time(self, seconds: float) -> None:
        """Let time go by without anybody waiting for it."""
        self.now += seconds


class RecordingFetcher:
    """A fetcher that answers instantly and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.asked.append(url)
        return FetchedPage(
            requested_url=url, final_url=url, status=200, body=b"", content_type="text/html"
        )


def make_fetcher(time: FakeTime, **kwargs: object) -> ThrottledFetcher:
    """Return a throttled fetcher over a recording one, on a fake clock."""
    schedule = kwargs.pop("schedule", None) or HostSchedule(clock=time.clock)
    return ThrottledFetcher(
        RecordingFetcher(),
        schedule=schedule,  # type: ignore[arg-type]
        waiter=time.wait,
        **kwargs,  # type: ignore[arg-type]
    )


# --- what counts as one server -----------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/a", "example.org"),
        ("https://EXAMPLE.org/a", "example.org"),
        ("http://example.org/a", "example.org"),
        ("https://example.org:8443/a", "example.org:8443"),
        ("https://cdn.example.org/a", "cdn.example.org"),
        ("mailto:someone@example.org", None),
    ],
)
def test_a_host_and_port_are_one_server(url: str, expected: str | None) -> None:
    assert host_key(url) == expected


def test_two_schemes_on_one_host_are_one_server() -> None:
    """One machine answering on two ports, not two machines to hammer."""
    assert host_key("http://example.org/a") == host_key("https://example.org/a")


# --- the schedule ------------------------------------------------------------


def test_the_first_request_to_a_host_waits_for_nothing() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    assert schedule.reserve("https://example.org/a", 2.0) == 0.0


def test_the_second_request_waits_the_delay() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://example.org/a", 2.0)

    assert schedule.reserve("https://example.org/b", 2.0) == 2.0


def test_time_already_spent_counts_against_the_wait() -> None:
    """A slow fetch has already been polite; it must not pay twice."""
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://example.org/a", 10.0)
    time.pass_time(6.0)

    assert schedule.reserve("https://example.org/b", 10.0) == 4.0


def test_a_host_left_alone_long_enough_waits_for_nothing() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://example.org/a", 2.0)
    time.pass_time(30.0)

    assert schedule.reserve("https://example.org/b", 2.0) == 0.0


def test_hosts_do_not_wait_for_each_other() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://one.test/a", 5.0)

    assert schedule.reserve("https://two.test/a", 5.0) == 0.0


def test_slots_queue_rather_than_collide() -> None:
    """Booked before the caller waits, so two threads line up behind each other."""
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    waits = [schedule.reserve("https://example.org/", 2.0) for _ in range(4)]

    assert waits == [0.0, 2.0, 4.0, 6.0]


def test_no_delay_reserves_nothing() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://example.org/a", 0.0)

    assert schedule.reserve("https://example.org/b", 0.0) == 0.0


def test_a_url_without_a_host_is_not_something_to_space_out() -> None:
    time = FakeTime()

    assert HostSchedule(clock=time.clock).reserve("mailto:a@b.test", 5.0) == 0.0


def test_a_schedule_can_be_emptied() -> None:
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)

    schedule.reserve("https://example.org/a", 5.0)
    schedule.clear()

    assert schedule.reserve("https://example.org/b", 5.0) == 0.0


# --- the fetcher -------------------------------------------------------------


def test_the_default_fetcher_waits_for_nothing() -> None:
    """What MaxiCrawler ships: no artificial delay nobody asked for."""
    time = FakeTime()
    fetcher = make_fetcher(time)

    for path in ("/a", "/b", "/c"):
        fetcher.fetch(f"https://example.org{path}")

    assert time.waited == []


def test_a_minimum_spaces_requests_to_one_host() -> None:
    time = FakeTime()
    fetcher = make_fetcher(time, minimum=1.5)

    fetcher.fetch("https://example.org/a")
    fetcher.fetch("https://example.org/b")

    assert time.waited == [1.5]


def test_a_minimum_does_not_space_requests_to_different_hosts() -> None:
    time = FakeTime()
    fetcher = make_fetcher(time, minimum=1.5)

    fetcher.fetch("https://one.test/a")
    fetcher.fetch("https://two.test/a")

    assert time.waited == []


def test_a_host_asking_for_more_is_obeyed() -> None:
    """A site may ask us to be slower than we planned."""
    time = FakeTime()
    fetcher = make_fetcher(time, minimum=1.0, delay_for=lambda url: 4.0)

    fetcher.fetch("https://example.org/a")
    fetcher.fetch("https://example.org/b")

    assert time.waited == [4.0]


def test_a_host_asking_for_less_does_not_speed_us_up() -> None:
    """Never faster than the operator configured."""
    time = FakeTime()
    fetcher = make_fetcher(time, minimum=3.0, delay_for=lambda url: 0.5)

    fetcher.fetch("https://example.org/a")
    fetcher.fetch("https://example.org/b")

    assert time.waited == [3.0]


def test_a_host_asking_for_nothing_leaves_the_minimum() -> None:
    time = FakeTime()
    fetcher = make_fetcher(time, minimum=2.0, delay_for=lambda url: None)

    fetcher.fetch("https://example.org/a")
    fetcher.fetch("https://example.org/b")

    assert time.waited == [2.0]


def test_a_delay_asked_for_by_one_host_does_not_slow_another() -> None:
    time = FakeTime()
    delays = {"slow.test": 10.0}
    fetcher = make_fetcher(time, delay_for=lambda url: delays.get(url.split("/")[2]))

    fetcher.fetch("https://slow.test/a")
    fetcher.fetch("https://fast.test/a")
    fetcher.fetch("https://fast.test/b")

    assert time.waited == []


def test_the_page_is_handed_back_unchanged() -> None:
    time = FakeTime()
    inner = RecordingFetcher()
    fetcher = ThrottledFetcher(inner, schedule=HostSchedule(clock=time.clock), waiter=time.wait)

    page = fetcher.fetch("https://example.org/a")

    assert page.final_url == "https://example.org/a"
    assert inner.asked == ["https://example.org/a"]


def test_a_throttled_fetcher_is_still_a_page_fetcher() -> None:
    assert isinstance(ThrottledFetcher(RecordingFetcher()), PageFetcher)


def test_two_fetchers_sharing_a_schedule_space_each_other() -> None:
    """How robots.txt is fetched politely without asking robots.txt about itself.

    The page fetcher learns its delay from `RobotsPolicy`; the robots fetcher
    cannot, because that is the request being made. Sharing the schedule is
    what keeps the second one from jumping the queue.
    """
    time = FakeTime()
    schedule = HostSchedule(clock=time.clock)
    pages = make_fetcher(time, schedule=schedule, minimum=2.0)
    robots = make_fetcher(time, schedule=schedule, minimum=2.0)

    robots.fetch("https://example.org/robots.txt")
    pages.fetch("https://example.org/a")

    assert time.waited == [2.0]


def test_a_negative_minimum_is_refused() -> None:
    with pytest.raises(ValueError, match="minimum"):
        ThrottledFetcher(RecordingFetcher(), minimum=-1.0)
