import re


def test_documentation_policy_and_authorization_schema(client):
    nonces = []
    for path in ("/docs", "/docs", "/redoc"):
        response = client.get(path)
        assert response.status_code == 200
        nonce = re.search(r'<script nonce="([^"]+)"', response.text).group(1)
        nonces.append(nonce)
        policy = response.headers["Content-Security-Policy"]
        assert f"'nonce-{nonce}'" in policy
        assert "https://cdn.jsdelivr.net" in policy
        assert "https://cdn.redoc.ly" in policy
        assert response.headers["Cache-Control"] == "no-store"
        assert "https://fastapi.tiangolo.com/img/favicon.png" not in response.text
        assert "fonts.googleapis.com" not in response.text
    assert len(set(nonces)) == 3
    workspace_policy = client.get("/").headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in workspace_policy and "unsafe-inline" not in workspace_policy
    schema = client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert schema["paths"]["/api/search"]["post"]["security"] == [{"APIKeyHeader": []}]
    assert not schema["paths"]["/api/health"]["get"].get("security")
