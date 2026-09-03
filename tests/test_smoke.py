def test_smoke(client):
    assert client.get("/").status_code in (200, 302)
