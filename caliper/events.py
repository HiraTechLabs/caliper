import queue
import threading


class EventBus:
    """In-process fan-out for request-state changes.

    The SSE endpoint subscribes a queue per browser and the request store
    publishes the committed row after every write. Artifacts are read fresh
    when the event is sent; a bounded queue drops old tokens for slow clients.
    """

    def __init__(self):
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, event: dict | None = None) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event or {})
            except queue.Full:
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(event or {})
                except queue.Full:
                    pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
