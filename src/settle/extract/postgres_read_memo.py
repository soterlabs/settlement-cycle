"""Bounded, capture-scoped copies of payloads already read from Postgres.

Entries are serialized JSON bytes, not decoded mutable objects. Only successful
raw_data SELECTs populate this cache; writes, misses and failures never do.
"""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Any


class ReadMemo:
    def __init__(self, max_entries: int, max_bytes: int):
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("Read memo limits must be positive")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.hits = 0
        self.misses = 0
        self.stored_bytes = 0
        self._entries: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._connection: Any = None
        self._database_url: str | None = None
        self._lock = RLock()

    def __repr__(self) -> str:
        return (
            f"ReadMemo(hits={self.hits}, misses={self.misses}, "
            f"max_entries={self.max_entries}, max_bytes={self.max_bytes})"
        )

    @staticmethod
    def _size(key: tuple[str, str], payload: bytes) -> int:
        # Serialized bytes plus key contents. Entry count separately bounds
        # the Python object/container overhead (not included in max_bytes).
        return len(payload) + sum(len(part.encode()) for part in key)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.stored_bytes = 0
            self._connection = None
            self._database_url = None

    def _bind(self, connection: Any, database_url: str | None) -> None:
        if connection is not self._connection or database_url != self._database_url:
            self.clear()
            # Holding the object prevents Python id reuse from aliasing a
            # later connection. Reconnects and configured database changes
            # require a fresh durable read; no credential is logged or saved.
            self._connection = connection
            self._database_url = database_url

    def get(self, connection: Any, database_url: str | None, key: tuple[str, str]) -> bytes | None:
        with self._lock:
            self._bind(connection, database_url)
            payload = self._entries.get(key)
            if payload is None:
                self.misses += 1
            else:
                self.hits += 1
                self._entries.move_to_end(key)
            return payload

    def remember(
        self, connection: Any, database_url: str | None, key: tuple[str, str], payload: bytes
    ) -> None:
        with self._lock:
            self._bind(connection, database_url)
            size = self._size(key, payload)
            if size > self.max_bytes:
                return
            previous = self._entries.pop(key, None)
            if previous is not None:
                self.stored_bytes -= self._size(key, previous)
            while self._entries and (
                len(self._entries) >= self.max_entries or self.stored_bytes + size > self.max_bytes
            ):
                old_key, old_payload = self._entries.popitem(last=False)
                self.stored_bytes -= self._size(old_key, old_payload)
            self._entries[key] = payload
            self.stored_bytes += size


_active_memo: ContextVar[ReadMemo | None] = ContextVar("postgres_read_memo", default=None)


def active_memo() -> ReadMemo | None:
    return _active_memo.get()


@contextmanager
def memoized_reads(
    *, max_entries: int = 32768, max_bytes: int = 64 * 1024 * 1024
) -> Iterator[ReadMemo]:
    """Reuse durable reads inside one capture; discard all entries on exit.

    The same full source/revision and argument hash form the memo key. A
    different connection or DATABASE_URL invalidates all prior entries.
    Nested contexts have independent entries. This context does not bypass
    connection availability checks or any required-persistence write.
    """
    memo = ReadMemo(max_entries, max_bytes)
    token = _active_memo.set(memo)
    try:
        yield memo
    finally:
        _active_memo.reset(token)
        memo.clear()
