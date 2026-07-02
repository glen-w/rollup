"""Tests for the benchmark helper script."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps({"response": f"summary for {payload['model']}"}).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


def test_benchmark_script_with_fake_endpoint(tmp_path: Path) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        json_out = tmp_path / "bench.json"
        md_out = tmp_path / "bench.md"
        script = Path(__file__).parent.parent / "scripts" / "benchmark_ollama_models.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--models",
                "model-a,model-b",
                "--runs",
                "1",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}/api/generate",
                "--out",
                str(json_out),
                "--markdown-out",
                str(md_out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert len(data["results"]) == 2
        assert md_out.exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)
