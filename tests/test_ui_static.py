def test_ui_index(client_ready):
    resp = client_ready.get("/ui/")
    assert resp.status_code == 200
    assert "Senserve" in resp.text
    assert "app.js" in resp.text


def test_ui_styles(client_ready):
    resp = client_ready.get("/ui/styles.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers.get("content-type", "")
