"""手机拍照上传服务：在局域网内提供一个网页，手机扫码打开后拍照上传到收件箱目录，并回显识别结果。"""
import hmac
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal

from app.paths import APP_DIR

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
PAGE_PATH = APP_DIR / "app" / "web" / "phone.html"


def local_addresses() -> List[str]:
    """本机可能被手机访问到的局域网 IPv4 地址，首选地址排在最前。"""
    found: List[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except OSError:
        pass
    return found


class PhoneServer(QObject):
    uploaded = Signal(str)  # 已保存的文件名

    def __init__(self, inbox: Path, parent=None):
        super().__init__(parent)
        self.inbox = Path(inbox)
        self.token = secrets.token_urlsafe(9)
        self.port = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._lock = threading.Lock()
        self._results: Dict[str, dict] = {}
        self._accepting = True
        self._counter = len(list(self.inbox.glob("phone_*.jpg"))) if self.inbox.exists() else 0

    def start(self) -> int:
        self.inbox.mkdir(parents=True, exist_ok=True)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self, query) -> bool:
                token = query.get("t", [""])[0]
                return hmac.compare_digest(token, owner.token)

            def _send(self, code, body: bytes, content_type="application/json; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if not self._authorized(query):
                    self._json(403, {"error": "forbidden"})
                elif parsed.path == "/":
                    self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
                elif parsed.path == "/status":
                    self._json(200, owner.status_of(query.get("id", [""])[0]))
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                parsed = urlparse(self.path)
                if not self._authorized(parse_qs(parsed.query)) or parsed.path != "/upload":
                    self._json(403, {"error": "forbidden"})
                    return
                if not owner._accepting:
                    self._json(503, {"error": "closed"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_UPLOAD_BYTES:
                    self._json(413, {"error": "bad size"})
                    return
                data = self.rfile.read(length)
                self._json(200, {"id": owner.save_upload(data)})

        self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def close_uploads(self):
        """不再接收新的上传（已上传的照片仍会继续识别并回显结果）。"""
        self._accepting = False

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def urls(self) -> List[str]:
        return [f"http://{address}:{self.port}/?t={self.token}" for address in local_addresses()]

    def save_upload(self, data: bytes) -> str:
        with self._lock:
            self._counter += 1
            name = f"phone_{self._counter:04d}.jpg"
            temp = self.inbox / (name + ".part")
            temp.write_bytes(data)
            temp.replace(self.inbox / name)
        self.uploaded.emit(name)
        return name

    def set_result(self, file: str, row: dict):
        """记录某张图的识别结果，供手机页面轮询显示。"""
        with self._lock:
            self._results[file] = row

    def status_of(self, file: str) -> dict:
        with self._lock:
            row = self._results.get(file)
        return {"state": "done", "row": row} if row else {"state": "pending"}
