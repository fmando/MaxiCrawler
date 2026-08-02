"""Tests for synchronous event delivery."""

from maxicrawler.domain import UrlRecord
from maxicrawler.events import EventBus, UrlDiscovered


def test_event_bus_subscribes_publishes_and_unsubscribes() -> None:
    bus = EventBus()
    received: list[UrlDiscovered] = []

    def handler(event: UrlDiscovered) -> None:
        received.append(event)

    bus.subscribe(UrlDiscovered, handler)
    event = UrlDiscovered(UrlRecord("https://example.test", "https://example.test/"))
    bus.publish(event)
    bus.unsubscribe(UrlDiscovered, handler)
    bus.publish(event)

    assert received == [event]
