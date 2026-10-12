import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.config import Settings


@pytest.mark.parametrize("env_file", [None, ".env.example"])
@pytest.mark.parametrize("origin", ["http://localhost:3001", "http://127.0.0.1:3001", "http://localhost:3000", "http://localhost:5173"])
def test_local_frontend_can_read_api_and_submit(env_file, origin):
    settings = Settings(_env_file=env_file)
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/bugs")
    def bugs():
        return {"items": []}

    client = TestClient(app)
    response = client.get("/api/bugs", headers={"Origin": origin})
    assert response.headers.get("access-control-allow-origin") == origin
    response = client.options("/api/agents/submit-task", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
