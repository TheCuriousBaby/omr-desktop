"""桌面程序入口：创建应用与主窗口。"""
import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.paths import APP_DIR
from app.settings import AppSettings
from app.ui.context import AppContext
from app.ui.main_window import MainWindow
from app.ui.theme import STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLESHEET)
    app.setWindowIcon(QIcon(str(APP_DIR / "assets" / "icon.png")))
    context = AppContext(AppSettings())
    window = MainWindow(context)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
