"""答案选择控件：阅卷、扫描时选用当前方案下的哪一套答案，或选择不评分。"""
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from app.ui.context import AppContext

NO_SCORING = ""


class AnswerPicker(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("使用答案"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(160)
        layout.addWidget(self.combo)
        ctx.projectChanged.connect(self.refresh)
        ctx.answersChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        previous = self.combo.currentData()
        self.combo.clear()
        project = self.ctx.project
        if project is None:
            return
        for name in project.answer_names() or [project.active_answer]:
            self.combo.addItem(name, name)
        self.combo.addItem("（不评分）", NO_SCORING)
        wanted = previous if previous is not None and self.combo.findData(previous) >= 0 else project.active_answer
        self.combo.setCurrentIndex(max(self.combo.findData(wanted), 0))

    def selected_path(self) -> Optional[Path]:
        """所选答案的文件路径；选择“不评分”或答案为空时返回 None。"""
        project, name = self.ctx.project, self.combo.currentData()
        if project is None or not name:
            return None
        path = project.answer_path(name)
        return path if path.exists() and path.stat().st_size > 2 else None

    def engine_options(self) -> dict:
        """传给识别引擎的评分参数。"""
        path = self.selected_path()
        return {"evaluation": path} if path else {"noeval": 1}
