"""取景画面中的答题卡检测：找出整张卡的四个角点，判断是否稳定，并按角点裁剪校正。"""
from typing import List, Optional, Tuple

import cv2
import numpy as np

MIN_AREA_RATIO = 0.2
MAX_AREA_RATIO = 0.97
BORDER_MARGIN = 0.02
RECT_FILL_MIN = 0.85


def order_corners(points: np.ndarray) -> np.ndarray:
    """按 左上、右上、右下、左下 排序四个点。"""
    points = np.asarray(points, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(diffs)]
    ordered[3] = points[np.argmax(diffs)]
    return ordered


def find_sheet(gray: np.ndarray) -> Optional[np.ndarray]:
    """在灰度图中找最大的亮色四边形（纸张），整张卡必须完整入画；找不到返回 None。返回像素坐标的四角点。"""
    height, width = gray.shape[:2]
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    frame_area = float(width * height)
    if not MIN_AREA_RATIO * frame_area <= area <= MAX_AREA_RATIO * frame_area:
        return None
    approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        corners = order_corners(approx)
    else:
        rect = cv2.minAreaRect(contour)
        if area / max(rect[1][0] * rect[1][1], 1.0) < RECT_FILL_MIN:
            return None
        corners = order_corners(cv2.boxPoints(rect))
    margin_x, margin_y = width * BORDER_MARGIN, height * BORDER_MARGIN
    if (
        corners[:, 0].min() < margin_x
        or corners[:, 1].min() < margin_y
        or corners[:, 0].max() > width - margin_x
        or corners[:, 1].max() > height - margin_y
    ):
        return None
    return corners


def warp_sheet(frame: np.ndarray, corners: np.ndarray, aspect: Optional[float] = None, long_side: int = 2000) -> np.ndarray:
    """按角点把纸张校正为正对的矩形；aspect 为 宽/高，缺省时按角点实际边长估算。"""
    tl, tr, br, bl = corners
    width = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    height = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    ratio = aspect if aspect else width / max(height, 1.0)
    if ratio >= 1:
        out_w, out_h = long_side, int(long_side / ratio)
    else:
        out_h, out_w = long_side, int(long_side * ratio)
    target = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(corners.astype("float32"), target)
    return cv2.warpPerspective(frame, matrix, (out_w, out_h))


class AutoCapture:
    """自动拍照状态机：卡片入画并保持静止一段时间后触发一次拍照；需卡片离开或明显移动后才会再次触发。"""

    def __init__(self, hold_seconds: float = 0.9, release_seconds: float = 0.7, tolerance: float = 0.012):
        self.hold_seconds = hold_seconds
        self.release_seconds = release_seconds
        self.tolerance = tolerance
        self.captured = False
        self._history: List[Tuple[float, np.ndarray]] = []
        self._absent_since: Optional[float] = None
        self._anchor: Optional[np.ndarray] = None

    def progress(self) -> float:
        """当前稳定进度 0~1，用于界面提示。"""
        if self.captured or len(self._history) < 2:
            return 0.0
        return min((self._history[-1][0] - self._history[0][0]) / self.hold_seconds, 1.0)

    def update(self, corners: Optional[np.ndarray], now: float, frame_width: float) -> bool:
        """传入本帧检测结果与时间；返回 True 表示此刻应当拍照。"""
        if corners is None:
            self._history.clear()
            if self._absent_since is None:
                self._absent_since = now
            if self.captured and now - self._absent_since >= self.release_seconds:
                self.captured = False
            return False
        self._absent_since = None
        if self.captured:
            if self._anchor is not None and np.abs(corners - self._anchor).max() > frame_width * 0.06:
                self.captured = False
                self._history.clear()
            return False
        self._history.append((now, corners))
        while self._history and now - self._history[0][0] > self.hold_seconds:
            self._history.pop(0)
        if now - self._history[0][0] < self.hold_seconds * 0.95 or len(self._history) < 3:
            return False
        stack = np.stack([c for _, c in self._history])
        if np.abs(stack - stack[-1]).max() > frame_width * self.tolerance:
            return False
        self.captured = True
        self._anchor = corners
        self._history.clear()
        return True
