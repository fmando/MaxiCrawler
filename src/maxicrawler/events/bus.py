"""A lightweight in-process synchronous event bus."""

from collections import defaultdict
from collections.abc import Callable

from maxicrawler.events.types import Event

type EventHandler = Callable[[Event], None]


class EventBus:
    """Delivers published events synchronously to matching subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[type[object], list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        """Register *handler* once for events of *event_type*."""
        handlers = self._subscribers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        """Remove *handler* if it was previously registered."""
        handlers = self._subscribers.get(event_type)
        if handlers is not None and handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        """Synchronously notify handlers for the event's concrete type."""
        for handler in tuple(self._subscribers.get(type(event), [])):
            handler(event)
