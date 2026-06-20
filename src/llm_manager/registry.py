"""Generic typed registry — formalizes the token_parsers/probes idiom.

Usage:
    parsers: Registry[str, TokenParser] = Registry()

    @parsers.register("v1/messages")
    def _parse(body: bytes) -> TokenUsage: ...
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")
D = TypeVar("D")


class UnknownKeyError(KeyError):
    """Raised by Registry.get when no default is given and the key is absent."""


_MISSING: object = object()
"""Module-level sentinel so a legitimately-registered ``None`` value is retrievable."""


class Registry(Generic[K, V]):
    """A decorator-keyed mapping from a discriminator to a value."""

    def __init__(self) -> None:
        self._items: dict[K, V] = {}

    def register(self, key: K) -> Callable[[V], V]:
        """Return a decorator that registers ``value`` under ``key``."""

        def _decorator(value: V) -> V:
            if key in self._items:
                raise ValueError(f"Registry key already registered: {key!r}")
            self._items[key] = value
            return value

        return _decorator

    def get(self, key: K, default: D = _MISSING) -> V | D:
        if key in self._items:
            return self._items[key]
        if default is not _MISSING:
            return default
        raise UnknownKeyError(key)

    def clear(self) -> None:
        """Remove all registrations (test/fixture helper)."""
        self._items.clear()

    def keys(self) -> frozenset[K]:
        return frozenset(self._items)

    def items(self) -> Iterable[tuple[K, V]]:
        return self._items.items()

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)
