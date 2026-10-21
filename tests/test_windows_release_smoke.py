"""Opt-in frozen binary smoke test, using only a loopback fixture upstream."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from roleplay_slim import __version__


def test_packaged_proxy_preserves_facts_and_streams(tmp_path):
    configured = os.environ.get("ROLEPLAY_SLIM_RELEASE_EXE")
    if not configured:
        pytest.skip("Set ROLEPLAY_SLIM_RELEASE_EXE to the freshly extracted proxy exe")
    exe = Path(configured).resolve(strict=True)
    version = subprocess.run(
        [str(exe), "--version"], capture_output=True, text=True, timeout=45
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"roleplay-slim-proxy {__version__}"
    captured = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append(body)
            if body.get("stream"):
                payload = 'data: {"choices":[{"delta":{"content":"测试通过"}}]}\n\ndata: [DONE]\n\n'.encode()
                content_type = "text/event-stream"
            else:
                payload = json.dumps(
                    {"choices": [{"message": {"content": "fixture-ok"}}]}
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = tmp_path / "fixture.toml"
    # Omit history_window_mode intentionally: test the frozen binary's default.
    config.write_text(
        f'[proxy]\nupstream_base_url="http://127.0.0.1:{upstream.server_port}/v1"\n'
        f'host="127.0.0.1"\nport={port}\nupstream_api_key_env="SLIM_RELEASE_FIXTURE_KEY"\n'
        "[compressor]\n[stats]\npersist=false\n",
        encoding="utf-8",
    )
    process = None
    fixture_env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    }
    fixture_env.update(
        {
            "SLIM_RELEASE_FIXTURE_KEY": "fixture-not-a-secret",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    try:
        process = subprocess.Popen(
            [str(exe), "--config", str(config)],
            cwd=tmp_path,
            env=fixture_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
        ) as client:
            deadline = time.monotonic() + 45
            while True:
                assert process.poll() is None, "Packaged proxy exited before readiness"
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < deadline, "Packaged proxy readiness timed out"
                time.sleep(0.1)
            messages = [
                {"role": "system", "content": "Persona: retain exactly."},
                {
                    "role": "user",
                    "content": "Opening. Do not buy peanuts: allergy. Closing.",
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": "Opening. Date corrected to Friday. Closing.",
                },
                {"role": "assistant", "content": "Noted."},
            ]
            for i in range(9):
                messages.extend(
                    [
                        {"role": "user", "content": f"Later {i}."},
                        {"role": "assistant", "content": "OK."},
                    ]
                )
            for stream in (False, True):
                response = client.post(
                    "/v1/chat/completions",
                    json={"model": "fixture", "messages": messages, "stream": stream},
                )
                assert response.status_code == 200
                assert (
                    ("[DONE]" in response.text)
                    if stream
                    else (
                        response.json()["choices"][0]["message"]["content"]
                        == "fixture-ok"
                    )
                )
                assert captured[-1]["messages"] == messages
            assert client.get("/stats").json()["request_count"] == 2
    finally:
        try:
            if process is not None:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=15,
                        )
                    else:
                        process.terminate()
                process.wait(timeout=15)
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)
