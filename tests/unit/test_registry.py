import pytest

from llm_manager.registry import Registry, UnknownKeyError


def test_register_via_decorator_and_lookup():
    reg: Registry[str, int] = Registry()

    @reg.register("a")
    def _a() -> int:
        return 1

    assert reg.get("a") is _a
    assert reg.get("a")() == 1


def test_get_unknown_returns_default():
    reg: Registry[str, int] = Registry()
    assert reg.get("missing", default=99) == 99


def test_get_unknown_without_default_raises():
    reg: Registry[str, int] = Registry()
    with pytest.raises(UnknownKeyError):
        reg.get("nope")


def test_keys_and_items():
    reg: Registry[str, int] = Registry()

    @reg.register("x")
    def _x() -> int:
        return 10

    assert reg.keys() == frozenset({"x"})
    assert dict(reg.items())["x"] is _x
