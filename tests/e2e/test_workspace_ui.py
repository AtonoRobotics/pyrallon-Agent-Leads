from __future__ import annotations

import threading
import urllib.error
import urllib.request

from buyer_ops_contracts.control_plane import serve


class _Plane:
    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes):
        return 200, {"method": method, "path": path}


def test_workspace_ui_is_served_without_exposing_arbitrary_files() -> None:
    server = serve("127.0.0.1", 0, _Plane())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/") as response:
            assert response.status == 200
            page = response.read().decode()
            assert "Agent workspace" in page
            assert "provider-oauth-form" in page
            assert "platform-oauth-client-form" in page
            assert "platform-oauth-client-secret" in page
            assert "google.workspace.calendar" in page
            assert "cognition-metered-form" in page
            assert "cognition-local-form" in page
        with urllib.request.urlopen(base + "/assets/app.js") as response:
            assert response.headers["Content-Type"] == "text/javascript"
            script = response.read().decode()
            assert "loadWorkspace" in script
            assert "/v1/connectors/oauth/start" in script
            assert "/v1/connectors/oauth/complete" in script
            assert "/v1/platform/oauth-clients" in script
            assert "renderPlatformOAuth" in script
            assert "completeOAuthCallback" in script
            assert "sessionStorage" in script
            assert "/v1/cognition/metered" in script
            assert "/v1/cognition/local" in script
        try:
            urllib.request.urlopen(base + "/assets/%2e%2e/pyproject.toml")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("UI server exposed a path outside the asset root")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
