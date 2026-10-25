"""主窗口：导航栏、方案管理条与各功能页。"""
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.demo import DEMO_NAME, install_demo
from app.project import Project, create_project, delete_project, duplicate_project, list_projects
from app.ui.answer_page import AnswerPage
from app.ui.context import AppContext
from app.ui.run_page import RunPage
from app.ui.scan_page import ScanPage
from app.ui.settings_page import SettingsPage
from app.ui.template_page import TemplatePage

INVALID_NAME = re.compile(r'[\\/:*?"<>|]')


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("OMR 阅卷助手")
        self.resize(1360, 860)
        self._loading = False
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(1200)
        self._autosave.timeout.connect(self._save_project)
        self._build_ui()
        settings = self.ctx.settings
        self._demo_sheets = Path(settings.get("app.output_root")).parent / "示例答题卡"
        demo_installed = install_demo(
            settings.get("app.project_root"), settings.path.parent, self._demo_sheets
        )
        self.refresh_projects()
        if demo_installed:
            QTimer.singleShot(400, self._show_welcome)

    def _build_ui(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(176)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        title = QLabel("OMR 阅卷助手")
        title.setObjectName("appTitle")
        sub = QLabel("答题卡识别与评分")
        sub.setObjectName("appSub")
        side_layout.addWidget(title)
        side_layout.addWidget(sub)

        self.stack = QStackedWidget()
        self.template_page = TemplatePage(self.ctx)
        self.answer_page = AnswerPage(self.ctx)
        self.run_page = RunPage(self.ctx, self.prepare_run)
        self.scan_page = ScanPage(self.ctx, self.prepare_run)
        self.settings_page = SettingsPage(self.ctx)
        pages = [
            ("版面模板", self.template_page),
            ("答案评分", self.answer_page),
            ("扫描采集", self.scan_page),
            ("开始阅卷", self.run_page),
            ("设置", self.settings_page),
        ]
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (text, page) in enumerate(pages):
            button = QPushButton(text)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, i=index: self.switch_page(i))
            self.nav_group.addButton(button, index)
            side_layout.addWidget(button)
            self.stack.addWidget(page)
        self.nav_group.button(0).setChecked(True)
        side_layout.addStretch(1)
        root.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)
        self.setCentralWidget(central)

        self.template_page.changed.connect(self._autosave.start)
        self.answer_page.changed.connect(self._autosave.start)
        self.ctx.settingsChanged.connect(self._on_settings_changed)

    def _build_header(self):
        header = QWidget()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(QLabel("当前方案"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(240)
        self.project_combo.activated.connect(self._on_project_chosen)
        layout.addWidget(self.project_combo)
        for text, callback in [
            ("新建", self.new_project),
            ("复制", self.copy_project),
            ("重命名", self.rename_project),
            ("删除", self.remove_project),
            ("打开目录", self.open_project_dir),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.empty_label = QLabel("还没有方案，请点击“新建”开始")
        self.empty_label.setStyleSheet("color:#b54708;")
        layout.addWidget(self.empty_label)
        layout.addStretch(1)
        self.save_label = QLabel("")
        self.save_label.setObjectName("hint")
        layout.addWidget(self.save_label)
        return header

    # ---------- 页面切换与保存 ----------

    def switch_page(self, index):
        self.stack.setCurrentIndex(index)
        if self.stack.widget(index) is self.answer_page:
            self.answer_page.refresh()

    def _save_project(self):
        project = self.ctx.project
        if not project:
            return
        project.save()
        self.save_label.setText("已自动保存")
        QTimer.singleShot(2500, lambda: self.save_label.setText(""))

    def prepare_run(self) -> str:
        """阅卷前保存并检查方案，返回错误说明；通过时返回空字符串。"""
        project = self.ctx.project
        if not project:
            return "请先选择或新建方案。"
        self._save_project()
        if not project.template.get("fieldBlocks"):
            return "当前方案还没有字段块，请先在“版面模板”页框选答题区域。"
        message = self.answer_page.validate()
        if message:
            return message
        return ""

    def _on_settings_changed(self):
        self.refresh_projects(keep_current=True)
        self.template_page.on_settings_changed()

    # ---------- 方案管理 ----------

    def refresh_projects(self, keep_current=False):
        root = Path(self.ctx.settings.get("app.project_root"))
        current = self.ctx.project.name if (keep_current and self.ctx.project) else None
        self._loading = True
        self.project_combo.clear()
        for directory in list_projects(root):
            self.project_combo.addItem(directory.name, str(directory))
        self._loading = False
        self.empty_label.setVisible(self.project_combo.count() == 0)
        if current and self.project_combo.findText(current) >= 0:
            self.project_combo.setCurrentIndex(self.project_combo.findText(current))
            return
        if self.project_combo.count():
            self.project_combo.setCurrentIndex(0)
            self._open_project(self.project_combo.itemData(0))
        else:
            self.ctx.set_project(None)

    def _show_welcome(self):
        QMessageBox.information(
            self,
            "欢迎使用",
            f"已为你准备好“{DEMO_NAME}”和一张示例答题卡。\n\n"
            "1. 在“版面模板”页查看字段块如何框在答题卡上；\n"
            f"2. 到“开始阅卷”页，添加文件夹\n{self._demo_sheets}\n后点击“开始阅卷”。\n\n"
            "识别参数、输出目录等可在“设置”页调整。",
        )

    def _on_project_chosen(self, index):
        if self._loading:
            return
        self._save_project()
        self._open_project(self.project_combo.itemData(index))

    def _open_project(self, directory):
        try:
            project = Project(directory).load()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "打开方案失败", str(error))
            return
        self.ctx.set_project(project)

    def _ask_name(self, title, default=""):
        name, ok = QInputDialog.getText(self, title, "方案名称", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if INVALID_NAME.search(name):
            QMessageBox.warning(self, "名称不合法", '名称不能包含 \\ / : * ? " < > | 等字符。')
            return None
        return name

    def _select_by_name(self, name):
        self.refresh_projects()
        index = self.project_combo.findText(name)
        if index >= 0:
            self.project_combo.setCurrentIndex(index)
            self._open_project(self.project_combo.itemData(index))

    def new_project(self):
        name = self._ask_name("新建方案")
        if not name:
            return
        self._save_project()
        settings = self.ctx.settings
        dims = (
            int(settings.get("dimensions.processing_width")),
            int(settings.get("dimensions.processing_height")),
        )
        try:
            create_project(settings.get("app.project_root"), name, dims)
        except FileExistsError as error:
            QMessageBox.warning(self, "新建方案", str(error))
            return
        self._select_by_name(name)
        self.nav_group.button(0).click()

    def copy_project(self):
        project = self.ctx.project
        if not project:
            return
        name = self._ask_name("复制方案", f"{project.name} 副本")
        if not name:
            return
        self._save_project()
        try:
            duplicate_project(project, name)
        except FileExistsError as error:
            QMessageBox.warning(self, "复制方案", str(error))
            return
        self._select_by_name(name)

    def rename_project(self):
        project = self.ctx.project
        if not project:
            return
        name = self._ask_name("重命名方案", project.name)
        if not name or name == project.name:
            return
        self._save_project()
        target = project.dir.parent / name
        if target.exists():
            QMessageBox.warning(self, "重命名", f"方案已存在：{name}")
            return
        project.dir.rename(target)
        self._select_by_name(name)

    def remove_project(self):
        project = self.ctx.project
        if not project:
            return
        answer = QMessageBox.question(
            self, "删除方案", f"确定删除方案“{project.name}”及其模板、答案文件吗？此操作不可恢复。\n（已生成的阅卷结果不受影响。）"
        )
        if answer != QMessageBox.Yes:
            return
        self._autosave.stop()
        self.ctx.set_project(None)
        delete_project(project)
        self.refresh_projects()

    def open_project_dir(self):
        if self.ctx.project:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.project.dir)))

    def closeEvent(self, event: QCloseEvent):
        self.scan_page.shutdown()
        self._save_project()
        super().closeEvent(event)
