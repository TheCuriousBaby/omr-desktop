"""自动识别版面：在预处理后的答题卡灰度图上找出气泡，按网格聚成字段块。"""
from typing import Dict, List, Optional

import cv2
import numpy as np

MIN_BUBBLE = 8
MAX_BUBBLE_RATIO = 0.15
LINK_RATIO = 2.4
LINK_SCALE = 1.6
MIN_HOLLOW_MEDIAN = 0.12
ALIGN_RATIO = 0.45
MIN_BLOCK_BUBBLES = 4


SETTINGS = [(block, c) for block in (21, 41, 71, 121) for c in (6, 12, 20)]


def _binarize(gray: np.ndarray, block: int = 31, c: int = 12) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, c
    )
    return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def _candidates(gray: np.ndarray, block: int = 31, c: int = 12) -> List[tuple]:
    """返回候选气泡外接框 (x, y, w, h, 空心比例)；空心比例为内部空洞面积与外接框面积之比。"""
    page_h, page_w = gray.shape[:2]
    max_side = min(page_w, page_h) * MAX_BUBBLE_RATIO
    contours, hierarchy = cv2.findContours(
        _binarize(gray, block, c), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]
    hole_area: Dict[int, float] = {}
    for index, contour in enumerate(contours):
        parent = hierarchy[index][3]
        if parent >= 0:
            hole_area[parent] = hole_area.get(parent, 0.0) + cv2.contourArea(contour)
    boxes = []
    for index, contour in enumerate(contours):
        if hierarchy[index][3] >= 0:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < MIN_BUBBLE or h < MIN_BUBBLE or w > max_side or h > max_side:
            continue
        if not 0.55 <= w / h <= 1.8:
            continue
        outer = cv2.contourArea(contour)
        if outer / float(w * h) < 0.5:
            continue
        boxes.append((x, y, w, h, hole_area.get(index, 0.0) / float(w * h)))
    return boxes


def _dominant_size(boxes: List[tuple]) -> Optional[tuple]:
    """取数量最多的尺寸簇的中位数宽高。"""
    if not boxes:
        return None
    sizes = np.array([(b[2], b[3]) for b in boxes], dtype=float)
    best, best_count = None, 0
    for w, h in sizes:
        mask = (np.abs(sizes[:, 0] - w) <= max(2, w * 0.15)) & (np.abs(sizes[:, 1] - h) <= max(2, h * 0.15))
        if mask.sum() > best_count:
            best, best_count = sizes[mask], int(mask.sum())
    return float(np.median(best[:, 0])), float(np.median(best[:, 1]))


def _neighbor_spacing(centers: np.ndarray, bw: float, bh: float) -> tuple:
    """估计同行相邻气泡的横向间距和同列相邻气泡的纵向间距（取最近邻间距的中位数）。"""
    right, below = [], []
    for i, (x, y) in enumerate(centers):
        dx = centers[:, 0] - x
        dy = centers[:, 1] - y
        in_row = (np.abs(dy) <= bh * ALIGN_RATIO) & (dx > bw * 0.5)
        in_col = (np.abs(dx) <= bw * ALIGN_RATIO) & (dy > bh * 0.5)
        if in_row.any():
            right.append(dx[in_row].min())
        if in_col.any():
            below.append(dy[in_col].min())
    sx = float(np.median(right)) if right else bw * (LINK_RATIO / LINK_SCALE)
    sy = float(np.median(below)) if below else bh * (LINK_RATIO / LINK_SCALE)
    return sx, sy


def _components(centers: np.ndarray, bw: float, bh: float) -> List[List[int]]:
    """按网格邻接关系把气泡连成块；连接阈值取实际行列间距的 LINK_SCALE 倍。"""
    n = len(centers)
    sx, sy = _neighbor_spacing(centers, bw, bh)
    reach_x = max(sx * LINK_SCALE, bw * 1.3)
    reach_y = max(sy * LINK_SCALE, bh * 1.3)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = np.argsort(centers[:, 0])
    for a_pos in range(n):
        a = order[a_pos]
        for b_pos in range(a_pos + 1, n):
            b = order[b_pos]
            dx = centers[b, 0] - centers[a, 0]
            if dx > max(reach_x, bw * ALIGN_RATIO):
                break
            dy = abs(centers[b, 1] - centers[a, 1])
            same_row = dy <= bh * ALIGN_RATIO and dx <= reach_x
            same_col = dx <= bw * ALIGN_RATIO and dy <= reach_y
            if same_row or same_col:
                parent[find(a)] = find(b)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _cluster(values: np.ndarray, tolerance: float) -> List[float]:
    values = np.sort(values)
    clusters: List[List[float]] = [[values[0]]]
    for value in values[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [float(np.mean(c)) for c in clusters]


def _trim_text_lines(indices: List[int], boxes: List[tuple], bw: float, bh: float) -> List[int]:
    """块内若某列（行）几乎全是非空心图形，而其余列（行）基本都是空心环，则视为题号文字并剔除。"""
    median_hole = float(np.median([boxes[i][4] for i in indices]))
    if median_hole < MIN_HOLLOW_MEDIAN:
        return indices
    threshold = median_hole * 0.5
    for axis, size in ((0, bw), (1, bh)):
        while True:
            centers = np.array([boxes[i][axis] + boxes[i][axis + 2] / 2 for i in indices])
            lines = _cluster(centers, size * ALIGN_RATIO)
            if len(lines) < 3:
                break
            fractions = []
            for line in lines:
                members = [i for i, c in zip(indices, centers) if abs(c - line) <= size * ALIGN_RATIO]
                fractions.append(np.mean([boxes[i][4] >= threshold for i in members]))
            hollow_lines = [f for f in fractions if f >= 0.8]
            text_lines = [k for k, f in enumerate(fractions) if f <= 0.2]
            if len(hollow_lines) < 2 or len(hollow_lines) < len(lines) - len(text_lines) or not text_lines:
                break
            drop = lines[text_lines[0]]
            indices = [i for i, c in zip(indices, centers) if abs(c - drop) > size * ALIGN_RATIO]
            if len(indices) < MIN_BLOCK_BUBBLES:
                break
    return indices


def _describe(indices: List[int], boxes: List[tuple], bw: float, bh: float) -> dict:
    sub = [boxes[i] for i in indices]
    left = min(b[0] for b in sub)
    top = min(b[1] for b in sub)
    right = max(b[0] + b[2] for b in sub)
    bottom = max(b[1] + b[3] for b in sub)
    cols = _cluster(np.array([b[0] + b[2] / 2 for b in sub]), bw * ALIGN_RATIO)
    rows = _cluster(np.array([b[1] + b[3] / 2 for b in sub]), bh * ALIGN_RATIO)
    regularity = len(sub) / float(len(cols) * len(rows))
    return {
        "x": int(left),
        "y": int(top),
        "w": int(right - left),
        "h": int(bottom - top),
        "cols": len(cols),
        "rows": len(rows),
        "regularity": round(regularity, 2),
    }


def _classify(block: dict) -> dict:
    """根据行列数推断题型：选项数通常为 2~6，数字块为 10 行。"""
    cols, rows = block["cols"], block["rows"]
    if rows == 10 and cols > 6:
        kind, options, direction, labels = "digits", 10, "vertical", cols
    elif cols <= 6 and rows >= cols:
        kind, options, direction, labels = "choice", cols, "horizontal", rows
    elif rows <= 6 and cols > rows:
        kind, options, direction, labels = "choice", rows, "vertical", cols
    elif cols <= 6:
        kind, options, direction, labels = "choice", cols, "horizontal", rows
    else:
        kind, options, direction, labels = "choice", rows, "vertical", cols
    block.update({"kind": kind, "options": options, "direction": direction, "labels": labels})
    return block


def _reading_order(blocks: List[dict], bw: float) -> List[dict]:
    """先按左边缘分列（从左到右），列内自上而下。"""
    ordered = sorted(blocks, key=lambda b: b["x"])
    columns: List[List[dict]] = []
    for block in ordered:
        if columns and abs(block["x"] - columns[-1][0]["x"]) <= bw * 2:
            columns[-1].append(block)
        else:
            columns.append([block])
    result = []
    for column in columns:
        result.extend(sorted(column, key=lambda b: b["y"]))
    return result


def _detect_once(gray: np.ndarray, block: int, c: int) -> dict:
    boxes = _candidates(gray, block, c)
    size = _dominant_size(boxes)
    if size is None:
        return {"bubble": None, "blocks": [], "score": 0}
    bw, bh = size
    boxes = [
        b
        for b in boxes
        if abs(b[2] - bw) <= max(3, bw * 0.3) and abs(b[3] - bh) <= max(3, bh * 0.3)
    ]
    if len(boxes) < MIN_BLOCK_BUBBLES:
        return {"bubble": [round(bw), round(bh)], "blocks": [], "score": 0}
    centers = np.array([[b[0] + b[2] / 2, b[1] + b[3] / 2] for b in boxes])
    blocks = []
    score = 0
    for indices in _components(centers, bw, bh):
        if len(indices) < MIN_BLOCK_BUBBLES:
            continue
        indices = _trim_text_lines(indices, boxes, bw, bh)
        if len(indices) < MIN_BLOCK_BUBBLES:
            continue
        block_info = _describe(indices, boxes, bw, bh)
        if block_info["cols"] * block_info["rows"] < MIN_BLOCK_BUBBLES or block_info["regularity"] < 0.6:
            continue
        score += len(indices) * block_info["regularity"]
        blocks.append(_classify(block_info))
    return {
        "bubble": [round(bw), round(bh)],
        "blocks": _reading_order(blocks, bw),
        "score": score,
    }


def detect_layout(gray: np.ndarray) -> dict:
    """返回 {"bubble": [w, h], "blocks": [...]}；多组二值化参数中取网格最完整的一组。"""
    best = {"bubble": None, "blocks": [], "score": 0}
    for block, c in SETTINGS:
        result = _detect_once(gray, block, c)
        if result["score"] > best["score"]:
            best = result
    best.pop("score", None)
    return best
