import hashlib
import http.server
import threading

import pytest

from imagecleanuper.downloads import ModelFile, download
from imagecleanuper.log import UserError

DATA = bytes(range(256)) * 4000  # ~1 MB


class Handler(http.server.BaseHTTPRequestHandler):
    drop_first = False
    requests: list = []

    def do_GET(self):
        Handler.requests.append(self.headers.get("Range"))
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        body = DATA[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if Handler.drop_first:
            Handler.drop_first = False
            self.wfile.write(body[: len(body) // 2])  # connection drops halfway
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/model.onnx"
    srv.shutdown()


def test_download_resumes_after_drop(server, tmp_path):
    Handler.drop_first, Handler.requests = True, []
    item = ModelFile("model.onnx", server, hashlib.sha256(DATA).hexdigest(), len(DATA))
    p = download(item, tmp_path, timeout=5)
    assert p.read_bytes() == DATA
    assert Handler.requests[0] is None and Handler.requests[-1].startswith("bytes=")  # resumed with Range


def test_download_rejects_bad_fingerprint(server, tmp_path):
    Handler.drop_first, Handler.requests = False, []
    item = ModelFile("model.onnx", server, "0" * 64, len(DATA))
    with pytest.raises(UserError):
        download(item, tmp_path, retries=2, timeout=5)
    assert not (tmp_path / "model.onnx").exists()
    assert not (tmp_path / "model.onnx.partial").exists()
