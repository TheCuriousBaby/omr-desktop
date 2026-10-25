"""手机拍照上传服务：在局域网内提供一个网页，手机扫码打开后拍照上传；电脑自动检测并裁剪答题卡边框，确认后进入识别并回显结果。"""
import hmac
import json
import secrets
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal

from app.paths import APP_DIR
from app.sheet_detect import find_sheet, warp_sheet

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
PAGE_PATH = APP_DIR / "app" / "web" / "phone.html"
DETECT_WIDTH = 640
PREVIEW_WIDTH = 900
CROP_LONG_SIDE = 2400


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


def analyze_photo(data: bytes, aspect: Optional[float]):
    """解码照片并检测答题卡边框，返回 (原图, 裁剪图或 None, 带边框的预览 JPEG 字节, 是否找到边框)。"""
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("无法读取图片")
    height, width = frame.shape[:2]
    scale = DETECT_WIDTH / width
    small = cv2.resize(frame, (DETECT_WIDTH, int(height * scale)), interpolation=cv2.INTER_AREA)
    corners = find_sheet(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    preview_scale = min(PREVIEW_WIDTH / width, 1.0)
    preview = cv2.resize(frame, (int(width * preview_scale), int(height * preview_scale)), interpolation=cv2.INTER_AREA)
    crop = None
    if corners is not None:
        full_corners = corners / scale
        crop = warp_sheet(frame, full_corners, aspect, CROP_LONG_SIDE)
        polygon = (full_corners * preview_scale).astype(np.int32)
        cv2.polylines(preview, [polygon], True, (60, 200, 60), max(3, preview.shape[1] // 160))
    ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return frame, crop, encoded.tobytes() if ok else b"", corners is not None


def _encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("图片编码失败")
    return encoded.tobytes()


class PhoneServer(QObject):
    uploaded = Signal(str)  # 已确认并存入收件箱的文件名

    def __init__(self, inbox: Path, crop: bool = True, aspect: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.inbox = Path(inbox)
        self.crop = crop
        self.aspect = aspect
        self.staging = self.inbox.parent / "staging"
        self.token = secrets.token_urlsafe(9)
        self.port = 0
        self._server: Optional[ThreadingHTTPServer] = None
        self._lock = threading.Lock()
        self._results: Dict[str, dict] = {}
        self._pending: Dict[str, dict] = {}
        self._accepting = True
        self._staged = 0
        self._counter = len(list(self.inbox.glob("phone_*.jpg"))) if self.inbox.exists() else 0

    def start(self) -> int:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
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
                elif parsed.path in ("/preview", "/crop"):
                    data = owner.image_of(query.get("id", [""])[0], parsed.path[1:])
                    if data is None:
                        self._json(404, {"error": "not found"})
                    else:
                        self._send(200, data, "image/jpeg")
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if not self._authorized(query):
                    self._json(403, {"error": "forbidden"})
                    return
                if not owner._accepting:
                    self._json(503, {"error": "closed"})
                    return
                if parsed.path == "/prepare":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_UPLOAD_BYTES:
                        self._json(413, {"error": "bad size"})
                        return
                    try:
                        self._json(200, owner.prepare(self.rfile.read(length)))
                    except ValueError as error:
                        self._json(400, {"error": str(error)})
                elif parsed.path == "/confirm":
                    name = owner.confirm(query.get("id", [""])[0])
                    if name:
                        self._json(200, {"id": name})
                    else:
                        self._json(404, {"error": "unknown id"})
                elif parsed.path == "/discard":
                    owner.discard(query.get("id", [""])[0])
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "not found"})

        self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def close_uploads(self):
        """不再接收新的上传（已确认的照片仍会继续识别并回显结果）。"""
        self._accepting = False

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        shutil.rmtree(self.staging, ignore_errors=True)

    def urls(self) -> List[str]:
        return [f"http://{address}:{self.port}/?t={self.token}" for address in local_addresses()]

    # ---------- 预检、确认、放弃 ----------

    def prepare(self, data: bytes) -> dict:
        """检测边框并生成预览；照片先放在暂存区，等手机确认后才进入识别。"""
        frame, crop, preview, found = analyze_photo(data, self.aspect)
        with self._lock:
            self._staged += 1
            sid = f"s{self._staged}"
            self._pending[sid] = {
                "original": _encode_jpeg(frame),
                "crop": _encode_jpeg(crop) if crop is not None else None,
                "preview": preview,
                "found": found,
            }
        return {"id": sid, "found": found, "crop": found and self.crop}

    def image_of(self, sid: str, kind: str) -> Optional[bytes]:
        with self._lock:
            item = self._pending.get(sid)
        if not item:
            return None
        return item["preview"] if kind == "preview" else item["crop"]

    def confirm(self, sid: str) -> Optional[str]:
        """确认暂存的照片：按设置存入裁剪图或原图，返回收件箱文件名。"""
        with self._lock:
            item = self._pending.pop(sid, None)
            if item is None:
                return None
            data = item["crop"] if (self.crop and item["found"] and item["crop"]) else item["original"]
            self._counter += 1
            name = f"phone_{self._counter:04d}.jpg"
            temp = self.inbox / (name + ".part")
            temp.write_bytes(data)
            temp.replace(self.inbox / name)
        self.uploaded.emit(name)
        return name

    def discard(self, sid: str):
        with self._lock:
            self._pending.pop(sid, None)

    # ---------- 识别结果回显 ----------

    def set_result(self, file: str, row: dict):
        """记录某张图的识别结果，供手机页面轮询显示。"""
        with self._lock:
            self._results[file] = row

    def status_of(self, file: str) -> dict:
        with self._lock:
            row = self._results.get(file)
        return {"state": "done", "row": row} if row else {"state": "pending"}
