"""全局界面样式。"""

STYLESHEET = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
QMainWindow, QWidget#content { background: #f6f7f9; }
QWidget#sidebar { background: #1f2937; }
QLabel#appTitle { color: #ffffff; font-size: 17px; font-weight: 600; padding: 18px 16px 4px 16px; }
QLabel#appSub { color: #9ca3af; font-size: 11px; padding: 0 16px 14px 16px; }
QPushButton#nav {
    color: #d1d5db; background: transparent; border: none; text-align: left;
    padding: 11px 18px; font-size: 14px; border-left: 3px solid transparent;
}
QPushButton#nav:hover { background: #2b3544; }
QPushButton#nav:checked { color: #ffffff; background: #2b3544; border-left: 3px solid #4f8cff; }
QWidget#header { background: #ffffff; border-bottom: 1px solid #e5e7eb; }
QLabel#hint { color: #667085; font-size: 12px; }
QPushButton {
    background: #ffffff; border: 1px solid #d0d5dd; border-radius: 5px; padding: 6px 14px;
}
QPushButton:hover { background: #f2f4f7; }
QPushButton:disabled { color: #98a2b3; background: #f2f4f7; }
QPushButton#primary { background: #2f6fed; color: #ffffff; border: 1px solid #2f6fed; font-weight: 600; }
QPushButton#primary:hover { background: #255ed0; }
QToolButton {
    background: #ffffff; border: 1px solid #d0d5dd; border-radius: 5px; padding: 5px 10px;
}
QToolButton:hover { background: #f2f4f7; }
QToolButton:checked { background: #dbe8ff; border-color: #2f6fed; color: #1a4fc0; }
QGroupBox {
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px;
    margin-top: 14px; padding: 14px 10px 10px 10px;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #344054; font-weight: 600; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
    background: #ffffff; border: 1px solid #d0d5dd; border-radius: 4px; padding: 4px 6px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #2f6fed; }
QTableWidget, QListWidget { background: #ffffff; border: 1px solid #e5e7eb; gridline-color: #eef0f3; }
QHeaderView::section { background: #f2f4f7; border: none; border-bottom: 1px solid #e5e7eb; padding: 6px 8px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #e5e7eb; background: #ffffff; top: -1px; }
QTabBar::tab { padding: 8px 16px; background: #eef0f3; border: 1px solid #e5e7eb; border-bottom: none; }
QTabBar::tab:selected { background: #ffffff; }
QProgressBar { border: 1px solid #d0d5dd; border-radius: 4px; text-align: center; height: 18px; background: #ffffff; }
QProgressBar::chunk { background: #2f6fed; border-radius: 3px; }
"""
