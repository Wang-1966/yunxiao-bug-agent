import json
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config
from src.api.settings import router
from src.tools.yunxiao.openapi_client import YunxiaoClient


def test_config_round_trip_and_validation(tmp_path, monkeypatch):
    settings = config.Settings(_env_file=None, yunxiao_access_token="env-token", yunxiao_org_id="env-org")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr("src.api.settings.get_settings", lambda: settings)
    path = tmp_path / "yunxiao_config.json"
    monkeypatch.setattr(config, "YUNXIAO_CONFIG_FILE", path)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get('/api/settings/yunxiao').json() == {
        "api_base": "https://openapi-rdc.aliyuncs.com", "org_id": "env-org",
        "project_id": "", "token_configured": True,
    }
    payload = {"api_base": "https://openapi-rdc.aliyuncs.com", "org_id": "new-org", "project_id": "*"}
    in_flight = YunxiaoClient(settings)
    result = client.put('/api/settings/yunxiao', json=payload)
    assert result.status_code == 200
    assert "access_token" not in result.json()
    assert settings.yunxiao_org_id == "new-org"
    assert in_flight.settings.yunxiao_org_id == "env-org"
    assert json.loads(path.read_text())["access_token"] == "env-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert client.put('/api/settings/yunxiao', json={**payload, "access_token": "new-token"}).status_code == 200
    assert settings.yunxiao_access_token == "new-token"
    assert client.put('/api/settings/yunxiao', json={**payload, "api_base": "https://example.com"}).status_code == 422
    assert client.put('/api/settings/yunxiao', json={**payload, "org_id": " "}).status_code == 422
    settings.yunxiao_access_token = ""
    assert client.put('/api/settings/yunxiao', json=payload).status_code == 400


def test_saved_config_overrides_env_on_restart(tmp_path, monkeypatch):
    path = tmp_path / "yunxiao_config.json"
    path.write_text(json.dumps({"api_base": "https://openapi-rdc.aliyuncs.com", "org_id": "saved-org", "project_id": "*", "access_token": "saved-token"}))
    monkeypatch.setattr(config, "YUNXIAO_CONFIG_FILE", path)
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.yunxiao_org_id == "saved-org"
        assert settings.yunxiao_access_token == "saved-token"
    finally:
        config.get_settings.cache_clear()
