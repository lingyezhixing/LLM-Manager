from llm_manager.gateway.errors import ApiError, api_error_handler


def test_api_error_payload():
    err = ApiError(status_code=501, message="proxy not implemented", type="not_implemented")
    assert err.status_code == 501
    assert err.payload() == {"error": {"type": "not_implemented", "message": "proxy not implemented"}}


def test_handler_returns_jsonresponse():
    err = ApiError(status_code=400, message="bad", type="bad_request")
    resp = api_error_handler(request=None, exc=err)
    assert resp.status_code == 400
