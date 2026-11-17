import pytest


@pytest.fixture(autouse=True)
def disable_external_observability(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), 'langfuse_enabled', False)


@pytest.fixture(autouse=True)
def close_scripted_pi_models():
    yield
    from fake_agent import SERVERS
    while SERVERS:
        server = SERVERS.pop()
        server.shutdown()
        server.server_close()
