"""Small in-process caches with a per-entry time to live (the app's st.cache_data).

Each entry expires ``ttl`` seconds after it was stored (not at fixed clock
buckets), so a value fetched at 10:14 is not dropped at 10:15. Empty results
can be kept for a shorter ``negative_ttl``: a ticker without data is not
downloaded again on every request, but a transient provider failure is
retried soon. Exceptions are never cached. The least recently used entry is
evicted beyond ``maxsize``.
"""

from __future__ import annotations

import functools
from collections import OrderedDict
from threading import Lock
from time import monotonic
from typing import Any, Callable, Hashable

_REGISTRY: list["TtlCache"] = []


class TtlCache:
    """Thread-safe LRU mapping whose entries expire individually."""

    def __init__(self, ttl: float, maxsize: int, clock: Callable[[], float] = monotonic) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._clock = clock
        self._entries: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()
        self._lock = Lock()
        _REGISTRY.append(self)

    def get(self, key: Hashable, default=None):
        """Stored value of ``key`` if it has not expired, else ``default``."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return default
            expires, value = entry
            if self._clock() >= expires:
                del self._entries[key]
                return default
            self._entries.move_to_end(key)
            return value

    def put(self, key: Hashable, value, ttl: float | None = None) -> None:
        """Store ``value`` for ``ttl`` seconds (default: the cache's TTL)."""
        now = self._clock()
        with self._lock:
            self._entries[key] = (now + (self.ttl if ttl is None else ttl), value)
            self._entries.move_to_end(key)
            for old in [k for k, (expires, _) in self._entries.items() if expires <= now]:
                del self._entries[old]
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def ttl_cache(
    ttl: float,
    maxsize: int,
    *,
    negative_ttl: float | None = None,
    is_negative: Callable[[Any], bool] | None = None,
    copy: Callable[[Any], Any] | None = None,
    clock: Callable[[], float] = monotonic,
):
    """Memoize a function of hashable positional arguments with :class:`TtlCache`.

    Results for which ``is_negative`` is true are kept ``negative_ttl``
    seconds (not at all when it is ``None``). ``copy`` is applied to the
    stored value and to every returned hit, so callers may mutate them. The
    wrapper exposes ``cache`` and ``cache_clear()``. The function runs outside
    the lock, so a slow call does not block other keys.
    """

    def decorator(function):
        cache = TtlCache(ttl, maxsize, clock)
        missing = object()

        @functools.wraps(function)
        def wrapper(*args):
            value = cache.get(args, missing)
            if value is missing:
                value = function(*args)
                negative = is_negative is not None and is_negative(value)
                if not negative or negative_ttl is not None:
                    cache.put(args, copy(value) if copy else value, negative_ttl if negative else None)
                return value
            return copy(value) if copy else value

        wrapper.cache = cache
        wrapper.cache_clear = cache.clear
        return wrapper

    return decorator


def clear_all() -> None:
    """Empty every cache created through this module (tests, measurements)."""
    for cache in _REGISTRY:
        cache.clear()
