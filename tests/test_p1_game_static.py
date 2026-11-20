from __future__ import annotations

import threading
import urllib.error
import urllib.request

from threebody.web.server import create_console_server


def test_p1_game_is_served_without_replacing_science_console(tmp_path):
    server = create_console_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/game/") as response:
            body = response.read().decode()
            assert response.status == 200
            assert "三体星 · 行星探索" in body
            assert "/game/assets/" in body
        with urllib.request.urlopen(base + "/") as response:
            assert "三体星" in response.read().decode()
        try:
            urllib.request.urlopen(base + "/game/../server.py")
        except urllib.error.HTTPError as exc:
            assert exc.code in {404, 422}
        else:
            raise AssertionError("path traversal was not rejected")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
