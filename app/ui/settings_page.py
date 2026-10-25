"""设置页：按参数规格自动生成表单，编辑识别参数、输出选项与常规路径。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.settings import GROUPS, parse_pages
from app.ui.context import AppContext


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.editors = {}
        self._build_ui()
        self.load_values()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for title, fields in GROUPS:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setColumnStretch(1, 1)
            for row, field in enumerate(fields):
                grid.addWidget(QLabel(field.label), row, 0, Qt.AlignTop)
                editor = self._make_editor(field)
                grid.addWidget(editor, row, 1)
                if field.help:
                    help_label = QLabel(field.help)
                    help_label.setObjectName("hint")
                    help_label.setWordWrap(True)
                    grid.addWidget(help_label, row, 2)
                self.editors[field.key] = (field, editor)
            grid.setColumnStretch(2, 2)
            if title == "大模型辅助":
                test_row = QHBoxLayout()
                self.ai_test_button = QPushButton("测试连接")
                self.ai_test_button.clicked.connect(self._on_test_ai)
                self.ai_test_result = QLabel("")
                self.ai_test_result.setWordWrap(True)
                test_row.addWidget(self.ai_test_button)
                test_row.addWidget(self.ai_test_result, 1)
                grid.addLayout(test_row, len(fields), 0, 1, 3)
            body_layout.addWidget(box)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self._on_reset)
        save = QPushButton("保存设置")
        save.setObjectName("primary")
        save.clicked.connect(self._on_save)
        buttons.addWidget(reset)
        buttons.addWidget(save)
        outer.addLayout(buttons)

    def _make_editor(self, field):
        kind = field.kind
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(field.minimum), int(field.maximum))
        elif kind == "dpi":
            widget = QSpinBox()
            widget.setRange(0, int(field.maximum))
            widget.setSpecialValueText("自动")
        elif kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(field.minimum, field.maximum)
            widget.setDecimals(2)
            widget.setSingleStep(0.05)
        elif kind == "bool":
            widget = QCheckBox()
        elif kind == "enum":
            widget = QComboBox()
            for value, title in field.choices:
                widget.addItem(title, value)
        elif kind == "dir":
            widget = _DirEdit()
        else:
            widget = QLineEdit()
        if kind == "secret":
            widget.setEchoMode(QLineEdit.Password)
        widget.setMaximumWidth(420 if kind in ("dir", "pages", "text", "secret") else 160)
        if kind == "dir":
            widget.setMaximumWidth(520)
        return widget

    def load_values(self):
        settings = self.ctx.settings
        for key, (field, editor) in self.editors.items():
            value = settings.get(key)
            if field.kind in ("int", "dpi"):
                editor.setValue(int(value))
            elif field.kind == "float":
                editor.setValue(float(value))
            elif field.kind == "bool":
                editor.setChecked(bool(value))
            elif field.kind == "enum":
                editor.setCurrentIndex(max(editor.findData(value), 0))
            else:
                editor.setText(str(value))

    def _collect(self):
        values = {}
        for key, (field, editor) in self.editors.items():
            if field.kind in ("int", "dpi", "float"):
                values[key] = editor.value()
            elif field.kind == "bool":
                values[key] = editor.isChecked()
            elif field.kind == "enum":
                values[key] = editor.currentData()
            else:
                values[key] = editor.text().strip()
        return values

    def _on_save(self):
        values = self._collect()
        try:
            parse_pages(values["pdf_params.pdf_page"])
        except ValueError:
            QMessageBox.warning(self, "格式不正确", "读取页码格式不正确，请填写如 1、1,3 或 2-5。")
            return
        for key in ("app.output_root", "app.project_root"):
            if not values[key]:
                QMessageBox.warning(self, "路径为空", "输出目录和方案目录不能为空。")
                return
        for key, value in values.items():
            self.ctx.settings.set(key, value)
        self.ctx.settings.save()
        self.ctx.settingsChanged.emit()
        QMessageBox.information(self, "设置", "设置已保存。")

    def _on_test_ai(self):
        from app import ai_assist
        from app.settings import AppSettings
        from app.ui.widgets import Worker

        trial = AppSettings(path=self.ctx.settings.path)
        for key, value in self._collect().items():
            trial.set(key, value)
        if not ai_assist.configured(trial):
            self.ai_test_result.setText("请先填写接口地址、密钥和模型名称。")
            return
        self.ai_test_button.setEnabled(False)
        self.ai_test_result.setText("正在测试…")
        self._ai_worker = Worker(lambda: ai_assist.test_connection(trial))
        self._ai_worker.done.connect(self._on_test_ai_done)
        self._ai_worker.start()

    def _on_test_ai_done(self, result, error):
        self.ai_test_button.setEnabled(True)
        failure = error or result
        self.ai_test_result.setStyleSheet("color:#b42318;" if failure else "color:#067647;")
        self.ai_test_result.setText(failure if failure else "连接成功。")

    def _on_reset(self):
        answer = QMessageBox.question(self, "恢复默认", "将全部设置恢复为默认值（尚未保存），是否继续？")
        if answer != QMessageBox.Yes:
            return
        self.ctx.settings.reset()
        self.load_values()


class _DirEdit(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        button = QPushButton("浏览…")
        button.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择目录", self.edit.text())
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text()

    def setText(self, value):
        self.edit.setText(value)
