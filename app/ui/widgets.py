"""通用界面控件：可缩放的图片查看器。"""
from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class ImageViewer(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QColor("#e9ecef"))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._item = None
        self._fitted = True
        self._text_item = None

    def set_pixmap(self, pixmap: Optional[QPixmap]):
        self._scene.clear()
        self._item = None
        self._text_item = None
        self.resetTransform()
        if pixmap is None or pixmap.isNull():
            return
        self._item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._item.boundingRect())
        self.fit()

    def show_text(self, text: str):
        self._scene.clear()
        self._item = None
        self.resetTransform()
        self._text_item = self._scene.addText(text)
        self._text_item.setDefaultTextColor(QColor("#667085"))
        self._scene.setSceneRect(self._text_item.boundingRect())

    def fit(self):
        if self._item is not None:
            self.fitInView(self._item, Qt.KeepAspectRatio)
            self._fitted = True

    def zoom(self, factor: float):
        self._fitted = False
        self.scale(factor, factor)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier or self._item is not None:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()


class Worker(QThread):
    """在后台线程执行函数，完成后发出 (返回值, 错误说明)；无错误时错误说明为 None。"""

    done = Signal(object, object)

    def __init__(self, function):
        super().__init__()
        self._function = function

    def run(self):
        try:
            self.done.emit(self._function(), None)
        except Exception as error:
            self.done.emit(None, str(error))
