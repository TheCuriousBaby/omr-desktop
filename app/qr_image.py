"""二维码绘制：把文字转成 QImage，供界面显示。"""
import segno
from PySide6.QtGui import QColor, QImage, QPainter

QUIET_ZONE = 4


def qr_image(text: str, module_pixels: int = 8) -> QImage:
    matrix = segno.make(text, error="m").matrix
    size = len(matrix) + 2 * QUIET_ZONE
    image = QImage(size * module_pixels, size * module_pixels, QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QColor("black"))
    painter.setBrush(QColor("black"))
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                painter.drawRect((x + QUIET_ZONE) * module_pixels, (y + QUIET_ZONE) * module_pixels, module_pixels, module_pixels)
    painter.end()
    return image
