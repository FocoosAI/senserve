def test_health_ready(client_ready):
    resp = client_ready.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True


def test_chat_rejects_when_switching(client_switching):
    resp = client_switching.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3-vl-4b-awq",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "30"


def test_list_models(client_ready):
    resp = client_ready.get("/v1/models")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert "qwen3-vl-4b-awq" in ids
    assert "gemma-4-26b-a4b-it" in ids
