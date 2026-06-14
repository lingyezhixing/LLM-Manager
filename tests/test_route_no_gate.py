import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_route_request_no_longer_calls_validate_or_interface_plugin():
    """After Task 4, route_request must not reference validate_request or
    get_interface_plugin. Guards against leaving the gate in place."""
    src = open(os.path.join(REPO_ROOT, "core", "api_router.py"), encoding="utf-8").read()
    # Locate route_request body heuristically.
    start = src.index("async def route_request")
    body = src[start:]
    assert "validate_request" not in body, "route_request still calls validate_request"
    assert "get_interface_plugin" not in body, "route_request still uses get_interface_plugin"
