"""The package must install and run on its own: runtime deps, form posts, model calls.

This is deliberately a real subprocess test: a clean virtualenv, a real
`pip install` of this project, a real uvicorn server, one multipart form POST and
one real HTTP call to a local OpenAI-compatible endpoint. Only runtime
dependencies are available, so a missing one fails here.
"""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

os.environ["CALIPER_DATA_DIR"] = sys.argv[2]

import uvicorn

from caliper.main import create_app

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()

server = uvicorn.Server(uvicorn.Config(
    create_app(), host="127.0.0.1", port=port, log_level="warning",
))
threading.Thread(target=server.run, daemon=True).start()

base = f"http://127.0.0.1:{port}"
for _ in range(100):
    try:
        with urllib.request.urlopen(base + "/health", timeout=1) as response:
            assert response.status == 200
        break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit("the app did not start")

boundary = "caliper-test-boundary"
body = (
    f"--{boundary}\\r\\n"
    'Content-Disposition: form-data; name="name"\\r\\n\\r\\n'
    "Shop API\\r\\n"
    f"--{boundary}\\r\\n"
    'Content-Disposition: form-data; name="repo_url"\\r\\n\\r\\n'
    "https://github.com/example/shop-api\\r\\n"
    f"--{boundary}--\\r\\n"
).encode("utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


request = urllib.request.Request(
    base + "/projects/new",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
try:
    urllib.request.build_opener(NoRedirect).open(request, timeout=10)
    raise SystemExit("expected a redirect after the form POST")
except urllib.error.HTTPError as error:
    assert error.code == 303, (error.code, error.read())

from caliper.http_models import ChatClient

reply = ChatClient("clean-venv-model", sys.argv[1], timeout=20).post("ping")
assert reply == "OK", reply

server.should_exit = True
print("clean-venv-ok")
"""


class _ModelEndpoint(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_clean_virtualenv_install_serves_a_form_post_and_calls_the_model_endpoint(tmp_path):
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=180)
    python = venv / "bin" / "python"
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900,
    )
    assert install.returncode == 0, install.stderr

    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        probe = tmp_path / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        result = subprocess.run(
            [str(python), str(probe), base_url, str(tmp_path / "data")],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "clean-venv-ok" in result.stdout
