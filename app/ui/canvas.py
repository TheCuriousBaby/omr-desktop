"""模板编辑画布：显示预处理后的答题卡，叠加可拖动、可缩放的字段块与气泡位置。"""
from typing import Callable, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from app.project import Block

BLOCK_COLORS = [
    "#2f80ed",
    "#eb5757",
    "#27ae60",
    "#f2994a",
    "#9b51e0",
    "#00a3a3",
    "#c2185b",
    "#6d4c41",
]

MODE_SELECT = "select"
MODE_DRAW_BLOCK = "draw_block"
MODE_MEASURE_BUBBLE = "measure_bubble"
MODE_DRAW_TEXT = "draw_text"

_HANDLES = ["tl", "t", "tr", "r", "br", "b", "bl", "l"]


class BlockItem(QGraphicsObject):
    geometryChanged = Signal(object)
    clicked = Signal(object)

    def __init__(self, block: Block, color: str, dims_provider: Callable):
        super().__init__()
        self.block = block
        self.color = QColor(color)
        self.dims_provider = dims_provider
        self._drag_handle: Optional[str] = None
        self._drag_origin = QPointF()
        self._drag_rect = QRectF()
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(1)

    def block_rect(self) -> QRectF:
        b = self.block
        return QRectF(b.x, b.y, b.w, b.h)

    def boundingRect(self) -> QRectF:
        return self.block_rect().adjusted(-24, -30, 24, 24)

    def shape(self):
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.addRect(self.block_rect().adjusted(-8, -8, 8, 8))
        return path

    def _handle_points(self, rect: QRectF):
        cx, cy = rect.center().x(), rect.center().y()
        return {
            "tl": rect.topLeft(),
            "t": QPointF(cx, rect.top()),
            "tr": rect.topRight(),
            "r": QPointF(rect.right(), cy),
            "br": rect.bottomRight(),
            "b": QPointF(cx, rect.bottom()),
            "bl": rect.bottomLeft(),
            "l": QPointF(rect.left(), cy),
        }

    def _scale(self) -> float:
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def _hit_handle(self, pos: QPointF) -> Optional[str]:
        reach = 7 / max(self._scale(), 0.01)
        for name, point in self._handle_points(self.block_rect()).items():
            if abs(pos.x() - point.x()) <= reach and abs(pos.y() - point.y()) <= reach:
                return name
        return None

    def paint(self, painter: QPainter, option, widget=None):
        block = self.block
        selected = self.isSelected()
        bw, bh = block.bubble_dims or self.dims_provider()
        fill = QColor(self.color)
        fill.setAlpha(70 if selected else 40)
        painter.setPen(QPen(self.color, 1.2 if selected else 0.8))
        painter.setBrush(QBrush(fill))
        for row in block.bubble_origins(self.dims_provider()):
            for x, y in row:
                painter.drawRect(QRectF(x, y, bw, bh))

        rect = self.block_rect()
        outline = QPen(self.color, 2 if selected else 1)
        outline.setCosmetic(True)
        outline.setStyle(Qt.SolidLine if selected else Qt.DashLine)
        painter.setPen(outline)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

        scale = max(self._scale(), 0.01)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(int(12 / scale), 1))
        painter.setFont(font)
        painter.setPen(self.color)
        painter.drawText(rect.topLeft() + QPointF(0, -4 / scale), block.name)

        if selected:
            size = 5 / scale
            painter.setPen(QPen(Qt.white, 1))
            painter.setBrush(self.color)
            for point in self._handle_points(rect).values():
                painter.drawRect(QRectF(point.x() - size, point.y() - size, 2 * size, 2 * size))

    def hoverMoveEvent(self, event):
        self._update_cursor(event.pos())

    def _update_cursor(self, pos):
        handle = self._hit_handle(pos) if self.isSelected() else None
        cursors = {
            "tl": Qt.SizeFDiagCursor,
            "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor,
            "bl": Qt.SizeBDiagCursor,
            "t": Qt.SizeVerCursor,
            "b": Qt.SizeVerCursor,
            "l": Qt.SizeHorCursor,
            "r": Qt.SizeHorCursor,
        }
        self.setCursor(cursors.get(handle, Qt.SizeAllCursor))

    def mousePressEvent(self, event):
        self.clicked.emit(self)
        self._drag_handle = (self._hit_handle(event.pos()) if self.isSelected() else None) or "move"
        self._drag_origin = event.pos()
        self._drag_rect = self.block_rect()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drag_handle:
            return
        delta = event.pos() - self._drag_origin
        rect = QRectF(self._drag_rect)
        handle = self._drag_handle
        if handle == "move":
            rect.translate(delta)
        else:
            if "l" in handle:
                rect.setLeft(min(rect.left() + delta.x(), rect.right() - 1))
            if "r" in handle:
                rect.setRight(max(rect.right() + delta.x(), rect.left() + 1))
            if "t" in handle:
                rect.setTop(min(rect.top() + delta.y(), rect.bottom() - 1))
            if "b" in handle:
                rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + 1))
        self.prepareGeometryChange()
        block = self.block
        block.x, block.y, block.w, block.h = rect.x(), rect.y(), rect.width(), rect.height()
        self.update()
        self.geometryChanged.emit(self)

    def mouseReleaseEvent(self, event):
        self._drag_handle = None

    def refresh(self):
        self.prepareGeometryChange()
        self.update()


class SheetCanvas(QGraphicsView):
    blockSelected = Signal(int)
    blockGeometryChanged = Signal(int)
    rectDrawn = Signal(QRectF)
    bubbleMeasured = Signal(QRectF)
    textRectDrawn = Signal(QRectF)
    cursorMoved = Signal(int, int)
    nudged = Signal(int, int)
    deletePressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setBackgroundBrush(QColor("#e9ecef"))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._pixmap: Optional[QPixmap] = None
        self._items: List[BlockItem] = []
        self._text_items: list = []
        self._mode = MODE_SELECT
        self._rubber: Optional[QGraphicsRectItem] = None
        self._rubber_start = QPointF()
        self._dims_provider: Callable = lambda: (20, 20)
        self._scene.selectionChanged.connect(self._on_selection_changed)

    def set_dims_provider(self, provider: Callable):
        self._dims_provider = provider

    def set_mode(self, mode: str):
        self._mode = mode
        if mode == MODE_SELECT:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.viewport().setCursor(Qt.CrossCursor)

    def page_size(self):
        rect = self._scene.sceneRect()
        return int(rect.width()), int(rect.height())

    def set_image(self, pixmap: Optional[QPixmap], page_w: int, page_h: int):
        """设置背景图，并缩放到页面尺寸（模板坐标系）。"""
        self._pixmap = pixmap
        if self._pixmap_item:
            self._scene.removeItem(self._pixmap_item)
            self._pixmap_item = None
        if pixmap and not pixmap.isNull():
            scaled = pixmap
            if pixmap.width() != page_w or pixmap.height() != page_h:
                scaled = pixmap.scaled(
                    page_w, page_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
                )
            self._pixmap_item = self._scene.addPixmap(scaled)
            self._pixmap_item.setZValue(0)
        self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))

    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def set_text_regions(self, regions: list):
        """显示文字区域（姓名、班级等）：橙色虚线框加名称。"""
        for item in self._text_items:
            self._scene.removeItem(item)
        self._text_items = []
        pen = QPen(QColor("#e8590c"), 2)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        for region in regions:
            box = QGraphicsRectItem(QRectF(region["x"], region["y"], region["w"], region["h"]))
            box.setPen(pen)
            box.setBrush(QBrush(QColor(232, 89, 12, 28)))
            box.setZValue(3)
            self._scene.addItem(box)
            label = self._scene.addSimpleText(str(region["name"]))
            label.setBrush(QBrush(QColor("#e8590c")))
            label.setPos(region["x"], region["y"] - label.boundingRect().height())
            label.setZValue(3)
            self._text_items += [box, label]

    def set_blocks(self, blocks: List[Block], selected: int = -1):
        for item in self._items:
            self._scene.removeItem(item)
        self._items = []
        for index, block in enumerate(blocks):
            item = BlockItem(block, BLOCK_COLORS[index % len(BLOCK_COLORS)], self._dims_provider)
            item.setAcceptHoverEvents(True)
            item.clicked.connect(self._on_item_clicked)
            item.geometryChanged.connect(self._on_item_geometry)
            self._scene.addItem(item)
            self._items.append(item)
        self.select_index(selected)

    def refresh_blocks(self):
        for item in self._items:
            item.refresh()

    def select_index(self, index: int):
        self._scene.blockSignals(True)
        self._scene.clearSelection()
        for i, item in enumerate(self._items):
            item.setSelected(i == index)
            item.setZValue(2 if i == index else 1)
        self._scene.blockSignals(False)
        self._scene.update()

    def _on_selection_changed(self):
        pass

    def _on_item_clicked(self, item):
        index = self._items.index(item)
        self.select_index(index)
        self.blockSelected.emit(index)

    def _on_item_geometry(self, item):
        self.blockGeometryChanged.emit(self._items.index(item))

    def fit(self):
        rect = self._scene.sceneRect()
        if rect.width() > 0:
            self.fitInView(rect, Qt.KeepAspectRatio)

    def zoom(self, factor: float):
        self.scale(factor, factor)

    def reset_zoom(self):
        self.resetTransform()

    def keyPressEvent(self, event):
        step = 10 if event.modifiers() & Qt.ShiftModifier else 1
        moves = {
            Qt.Key_Left: (-step, 0),
            Qt.Key_Right: (step, 0),
            Qt.Key_Up: (0, -step),
            Qt.Key_Down: (0, step),
        }
        if event.key() in moves and any(item.isSelected() for item in self._items):
            self.nudged.emit(*moves[event.key()])
            event.accept()
        elif event.key() == Qt.Key_Delete and any(item.isSelected() for item in self._items):
            self.deletePressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self._mode != MODE_SELECT and event.button() == Qt.LeftButton:
            self._rubber_start = self._clamp(self.mapToScene(event.position().toPoint()))
            self._rubber = QGraphicsRectItem(QRectF(self._rubber_start, self._rubber_start))
            pen = QPen(QColor("#d6336c"), 1.5)
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine)
            self._rubber.setPen(pen)
            self._rubber.setZValue(10)
            self._scene.addItem(self._rubber)
            event.accept()
            return
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and self._mode == MODE_SELECT:
            under = self.items(event.position().toPoint())
            if not any(isinstance(item, BlockItem) for item in under):
                self.select_index(-1)
                self.blockSelected.emit(-1)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        self.cursorMoved.emit(int(scene_pos.x()), int(scene_pos.y()))
        if self._rubber is not None:
            current = self._clamp(scene_pos)
            self._rubber.setRect(QRectF(self._rubber_start, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubber is not None:
            rect = self._rubber.rect()
            self._scene.removeItem(self._rubber)
            self._rubber = None
            if rect.width() >= 3 and rect.height() >= 3:
                if self._mode == MODE_DRAW_BLOCK:
                    self.rectDrawn.emit(rect)
                elif self._mode == MODE_DRAW_TEXT:
                    self.textRectDrawn.emit(rect)
                elif self._mode == MODE_MEASURE_BUBBLE:
                    self.bubbleMeasured.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clamp(self, point: QPointF) -> QPointF:
        rect = self._scene.sceneRect()
        return QPointF(
            min(max(point.x(), rect.left()), rect.right()),
            min(max(point.y(), rect.top()), rect.bottom()),
        )
