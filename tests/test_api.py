import os

os.environ.setdefault("ASSEMBLYAI_API_KEY", "test-key")

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch):
    # Do not run ffmpeg/AssemblyAI in unit tests.
    monkeypatch.setattr(main, "process_video", lambda job_id, path: None)
    return TestClient(main.app)


def test_rejects_non_video(client):
    response = client.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400


def test_rejects_video_over_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    response = client.post("/upload", files={"file": ("clip.mp4", b"x" * 11, "video/mp4")})
    assert response.status_code == 413


def test_upload_creates_job_with_safe_path(client, monkeypatch):
    saved = {}
    monkeypatch.setattr(main, "process_video", lambda job_id, path: saved.update(path=path))
    response = client.post(
        "/upload", files={"file": ("../../evil name.mp4", b"data", "video/mp4")}
    )
    assert response.status_code == 200
    job_id = response.json()["jobId"]
    assert os.path.dirname(saved["path"]) == main.UPLOAD_DIR
    assert os.path.basename(saved["path"]) == f"video_ai_{job_id}.mp4"
    assert client.get(f"/status/{job_id}").json()["status"] == "processing"
    os.remove(saved["path"])


def test_unknown_job_returns_404(client):
    assert client.get("/status/does-not-exist").status_code == 404


def test_ask_validates_body(client):
    assert client.post("/ask", json={"question": "Why?"}).status_code == 422


def test_ask_requires_groq_key(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    response = client.post("/ask", json={"transcript": "Hello", "question": "Why?"})
    assert response.status_code == 500


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def test_groq_model_falls_back_to_an_available_model(monkeypatch):
    monkeypatch.setattr(main, "_resolved_groq_model", None)
    monkeypatch.setenv("GROQ_MODEL", "retired-model")
    models = {"data": [{"id": "whisper-large-v3"}, {"id": "qwen/qwen3-32b"}]}
    monkeypatch.setattr(main.requests, "get", lambda *a, **k: FakeResponse(models))
    assert main.resolve_groq_model("key") == "qwen/qwen3-32b"


def test_ask_uses_the_resolved_model(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.setattr(main, "_resolved_groq_model", "some-model")
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(json)
        return FakeResponse({"choices": [{"message": {"content": "It is about ML."}}]})

    monkeypatch.setattr(main.requests, "post", fake_post)
    response = client.post("/ask", json={"transcript": "Hello", "question": "Why?"})
    assert response.json() == {"answer": "It is about ML."}
    assert sent["model"] == "some-model"
