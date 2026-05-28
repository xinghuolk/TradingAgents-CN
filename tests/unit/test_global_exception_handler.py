"""Verifies global_exception_handler enriches the 500 envelope with diagnosis info
while leaving FastAPI's built-in HTTPException handling untouched."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exception_handler import build_global_exception_handler


def _make_app(debug: bool) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(Exception, build_global_exception_handler(debug=debug))

    @app.get("/runtime")
    def _runtime():
        raise RuntimeError("boom")

    @app.get("/http")
    def _http():
        raise HTTPException(status_code=404, detail="nope")

    @app.post("/only-post")
    def _only_post():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_unknown_exception_returns_500_envelope_with_exception_type():
    client = _make_app(debug=False)
    r = client.get("/runtime")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["exception_type"] == "RuntimeError"
    # Production: do NOT leak the message
    assert "boom" not in body["error"]["message"]
    assert body["error"]["message"] == "Internal server error occurred"


def test_unknown_exception_in_debug_mode_includes_exception_detail():
    client = _make_app(debug=True)
    r = client.get("/runtime")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["exception_type"] == "RuntimeError"
    # DEBUG: leak the full detail for diagnosis
    assert "boom" in body["error"]["message"]
    assert "RuntimeError" in body["error"]["message"]


def test_http_exception_uses_fastapi_default_not_our_envelope():
    client = _make_app(debug=False)
    r = client.get("/http")
    assert r.status_code == 404
    # FastAPI default body shape, not our envelope
    body = r.json()
    assert body == {"detail": "nope"}


def test_method_not_allowed_uses_fastapi_default():
    client = _make_app(debug=False)
    r = client.get("/only-post")
    assert r.status_code == 405
    body = r.json()
    assert body == {"detail": "Method Not Allowed"}
