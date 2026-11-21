from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from kadr.api import create_app  # noqa: E402
from kadr.catalog.repository import Catalog  # noqa: E402
from kadr.security.api_auth import ApiSecurity, is_loopback_host  # noqa: E402


@pytest.fixture()
def client(settings):
    catalog = Catalog(settings.catalog_path)
    app = create_app(settings, catalog)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client
    catalog.close()


def _headers(client, csrf: bool = False):
    token = client.app.state.settings.api_token
    headers = {"Authorization": f"Bearer {token}"}
    if csrf:
        headers["X-Kadr-CSRF"] = client.app.state.security.csrf_token
    return headers


def test_auth_required_and_csrf_for_mutations(client, tmp_path):
    assert client.get("/health").status_code == 200
    assert client.get("/v1/system/status").status_code == 401
    assert client.get("/v1/assets", headers=_headers(client)).status_code == 200
    assert (
        client.post("/v1/scan", json={"root": str(tmp_path)}, headers=_headers(client)).status_code
        == 403
    )
    token = client.app.state.settings.api_token
    assert client.post(
        "/v1/scan", json={"root": str(tmp_path)}, headers={**_headers(client), "X-Kadr-CSRF": token}
    ).status_code == 403
    assert client.app.state.security.csrf_token != token
    # Корректный CSRF + разрешённый корень из KADR_SCAN_ROOTS: запрос проходит
    # проверку безопасности (allowlist корней — отдельный слой, см. test_scan_root_allowlist).
    assert (
        client.post(
            "/v1/scan", json={"root": str(tmp_path)}, headers=_headers(client, csrf=True)
        ).status_code
        in {200, 400}
    )
    assert (
        client.post("/v1/scan", json={"root": "/etc"}, headers=_headers(client, csrf=True)).status_code
        == 403
    )


def test_metadata_upload_returns_l0_without_path_access(client, tmp_path):
    from PIL import Image

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (12, 9), color=(10, 20, 30)).save(image_path)
    payload = image_path.read_bytes()
    response = client.post(
        "/v1/metadata",
        files={"file": ("sample.png", payload, "image/png")},
        headers=_headers(client, csrf=True),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["width"] == 12
    assert data["height"] == 9


def test_origin_and_host_are_checked(client):
    headers = _headers(client)
    assert client.get("/v1/assets", headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/v1/assets", headers={**headers, "Host": "evil.example"}).status_code == 403
    assert client.get("/v1/assets", headers={**headers, "Origin": "http://127.0.0.1"}).status_code == 200
    assert client.get("/v1/assets", headers={**headers, "Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_api_security_unit():
    security = ApiSecurity(token="secret")
    assert security.authorize(method="GET", headers={"authorization": "Bearer secret"}, host="127.0.0.1:8000") is None
    assert security.authorize(method="GET", headers={}, host="127.0.0.1")[0] == 401
    assert security.authorize(method="POST", headers={"authorization": "Bearer secret"}, host="127.0.0.1")[0] == 403
    assert is_loopback_host("localhost:1234")
    assert is_loopback_host("[::1]:1234")
    assert not is_loopback_host("10.0.0.1:8000")


def test_analyze_endpoint_passes_run_ocr_flag(client, monkeypatch):
    import kadr.api as api

    seen: dict[str, object] = {}

    def fake_analyze(catalog, **kwargs):
        seen.update(kwargs)
        return {"processed": 0}

    monkeypatch.setattr(api, "analyze_catalog", fake_analyze)
    response = client.post(
        "/v1/analyze",
        json={"run_ocr_enabled": False},
        headers=_headers(client, csrf=True),
    )
    assert response.status_code == 200
    assert seen["run_ocr_enabled"] is False
    assert seen["ocr_language"] == client.app.state.settings.ocr_language


def test_asset_thumbnail_returns_jpeg(client, source_dir):
    """FR-5.1: миниатюра JPEG для галереи (сканирование → /v1/assets/{id}/thumbnail)."""
    headers = _headers(client, csrf=True)
    resp = client.post("/v1/scan", json={"root": str(source_dir)}, headers=headers)
    assert resp.status_code in {200, 400}
    resp = client.get("/v1/assets", headers=_headers(client))
    assert resp.status_code == 200
    items = resp.json()["items"]
    photo = next((a for a in items if a["kind"] == "photo"), None)
    assert photo is not None
    resp = client.get(f"/v1/assets/{photo['id']}/thumbnail", headers=_headers(client))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) > 0
