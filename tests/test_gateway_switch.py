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
    by_id = {m["id"]: m for m in resp.json()["data"]}
    assert "qwen3-vl-4b-awq" in by_id
    assert "gemma-4-26b-a4b-it" in by_id
    assert by_id["qwen3-vl-4b-awq"]["source"] == "Qwen/Qwen3-VL-4B-Instruct"
    assert by_id["qwen3-vl-4b-awq"]["status"] == "ready"
    assert by_id["qwen3-vl-4b-awq"]["loaded"] is True
    assert by_id["gemma-4-26b-a4b-it"]["status"] == "cold"
    assert by_id["gemma-4-26b-a4b-it"]["loaded"] is False
