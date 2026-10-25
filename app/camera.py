"""摄像头取景线程：实时预览、检测答题卡、手动或自动拍照并保存到收件箱目录。"""
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from app.sheet_detect import AutoCapture, find_sheet, warp_sheet

DETECT_WIDTH = 480
PREVIEW_WIDTH = 800
PREVIEW_INTERVAL = 1 / 20
MAX_READ_FAILURES = 60
RESOLUTIONS = [("摄像头默认", None), ("1280 × 720", (1280, 720)), ("1920 × 1080", (1920, 1080)), ("2560 × 1440", (2560, 1440)), ("3840 × 2160", (3840, 2160))]

Source = Union[int, str]


def open_capture(source: Source, size: Optional[Tuple[int, int]] = None):
    """打开摄像头（序号）或视频文件（路径）；可请求分辨率。"""
    if isinstance(source, int):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(str(source))
    if cap.isOpened() and isinstance(source, int):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if size:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
    return cap


def probe_cameras(max_index: int = 5) -> List[Tuple[int, str]]:
    """逐个尝试打开前几个摄像头序号，返回 [(序号, 显示名)]。"""
    found = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None:
                    found.append((index, f"摄像头 {index}（{frame.shape[1]}×{frame.shape[0]}）"))
        finally:
            cap.release()
    return found


def to_qimage(frame_bgr: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    return QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()


class CameraWorker(QThread):
    frameReady = Signal(QImage)
    statusChanged = Signal(str)
    captured = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        source: Source,
        inbox: Path,
        size: Optional[Tuple[int, int]] = None,
        auto: bool = False,
        crop: bool = False,
        aspect: Optional[float] = None,
        realtime: bool = True,
    ):
        super().__init__()
        self.source, self.inbox, self.size = source, Path(inbox), size
        self.auto, self.crop, self.aspect, self.realtime = auto, crop, aspect, realtime
        self._stop = False
        self._manual = False
        self.count = len(list(self.inbox.glob("scan_*.jpg"))) if self.inbox.exists() else 0

    def request_capture(self):
        self._manual = True

    def stop(self):
        self._stop = True

    def _save(self, frame: np.ndarray, corners: Optional[np.ndarray], scale: float) -> str:
        image = frame
        if self.crop and corners is not None:
            image = warp_sheet(frame, corners * scale, self.aspect)
        self.count += 1
        path = self.inbox / f"scan_{self.count:04d}.jpg"
        ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise OSError("图片编码失败")
        buffer.tofile(str(path))
        return str(path)

    def run(self):
        cap = open_capture(self.source, self.size)
        if not cap.isOpened():
            self.failed.emit("无法打开摄像头或视频，请检查设备是否被其他程序占用")
            return
        self.inbox.mkdir(parents=True, exist_ok=True)
        is_file = not isinstance(self.source, int)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        machine = AutoCapture()
        failures, index, last_preview = 0, 0, 0.0
        try:
            while not self._stop:
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file:
                        break
                    failures += 1
                    if failures > MAX_READ_FAILURES:
                        self.failed.emit("摄像头读取失败，请检查连接")
                        return
                    time.sleep(0.02)
                    continue
                failures = 0
                index += 1
                clock = index / fps if (is_file and not self.realtime) else time.monotonic()
                height, width = frame.shape[:2]
                scale_small = DETECT_WIDTH / width
                small = cv2.resize(frame, (DETECT_WIDTH, int(height * scale_small)), interpolation=cv2.INTER_AREA)
                corners = find_sheet(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
                scale_full = width / DETECT_WIDTH

                saved = None
                if self._manual:
                    self._manual = False
                    saved = self._save(frame, corners, scale_full)
                elif self.auto and machine.update(corners, clock, DETECT_WIDTH):
                    saved = self._save(frame, corners, scale_full)
                if saved:
                    self.captured.emit(saved)
                    self.statusChanged.emit(f"已拍照，共 {self.count} 张；请换下一张")

                if clock - last_preview >= PREVIEW_INTERVAL or not self.realtime:
                    last_preview = clock
                    preview = cv2.resize(frame, (PREVIEW_WIDTH, int(height * PREVIEW_WIDTH / width)), interpolation=cv2.INTER_AREA)
                    if corners is not None:
                        color = (0, 200, 0) if not (self.auto and machine.progress() < 1) else (0, 200, 255)
                        polygon = (corners * (PREVIEW_WIDTH / DETECT_WIDTH)).astype(np.int32)
                        cv2.polylines(preview, [polygon], True, color, 3)
                    self.frameReady.emit(to_qimage(preview))
                    if not saved:
                        self.statusChanged.emit(self._status_text(corners, machine))
                if is_file and self.realtime:
                    time.sleep(1.0 / fps)
        finally:
            cap.release()

    def _status_text(self, corners, machine: AutoCapture) -> str:
        if corners is None:
            return "未检测到完整的答题卡（四边需完整入画）" + ("" if self.auto else "；仍可手动拍照")
        if self.auto and not machine.captured:
            return f"已检测到答题卡，请保持不动… {int(machine.progress() * 100)}%"
        if machine.captured and self.auto:
            return f"已拍照，共 {self.count} 张；请换下一张"
        return "已检测到答题卡，可以拍照"
