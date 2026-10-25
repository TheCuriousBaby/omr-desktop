"""扫描采集页：用摄像头逐张拍照识别，或监控一个目录（手机、扫描仪保存的图片）自动识别。"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.engine_client import EngineJob, write_config
from app.text_fields_io import save_texts
from app.ui.answer_picker import AnswerPicker
from app.ui.context import AppContext
from app.ui.run_page import STATUS_COLOR, STATUS_TEXT
from app.ui.text_reader import TextReader
from app.ui.widgets import ImageViewer, Worker

DRAIN_TIMEOUT_SECONDS = 90
KILL_TIMEOUT_SECONDS = 15


class ScanPage(QWidget):
    def __init__(self, ctx: AppContext, prepare, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.prepare = prepare
        self.job = EngineJob(self)
        self.job.ready.connect(self._on_engine_ready)
        self.job.rowReady.connect(self._on_row)
        self.job.cropReady.connect(self._on_crop)
        self.job.logLine.connect(self._on_log)
        self.job.finished.connect(self._on_engine_finished)
        self.camera = None
        self.probe_worker: Optional[Worker] = None
        self.probed = False
        self.running = False
        self.run_dir: Optional[Path] = None
        self.inbox: Optional[Path] = None
        self.columns: List[str] = []
        self.rows: List[dict] = []
        self.statuses: Dict[str, str] = {}
        self.captured_count = 0
        self.text_fields: List[str] = []
        self.texts: Dict[str, Dict[str, str]] = {}
        self._filling = False
        self._text_note_shown = False
        self.reader = TextReader(ctx.settings, self)
        self.reader.textReady.connect(self._on_text_ready)
        self.reader.failed.connect(lambda text: self.show_message(f"大模型识别文字失败：{text}", error=True))
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(300)
        self._drain_timer.timeout.connect(self._check_drained)
        self._drain_elapsed = 0.0
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self.job.cancel)
        self._build_ui()
        ctx.projectChanged.connect(self._on_project_changed)

    # ---------- 界面 ----------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        source_row = QHBoxLayout()
        self.radio_camera = QRadioButton("摄像头")
        self.radio_folder = QRadioButton("监控文件夹")
        self.radio_camera.setChecked(True)
        self.radio_camera.toggled.connect(self._update_mode)
        source_row.addWidget(QLabel("来源"))
        source_row.addWidget(self.radio_camera)
        source_row.addWidget(self.radio_folder)
        source_row.addStretch(1)
        root.addLayout(source_row)

        self.camera_box = QWidget()
        camera_row = QHBoxLayout(self.camera_box)
        camera_row.setContentsMargins(0, 0, 0, 0)
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(200)
        self.btn_probe = QPushButton("刷新摄像头")
        self.btn_probe.clicked.connect(self.probe_cameras)
        self.size_combo = QComboBox()
        from app.camera import RESOLUTIONS

        for title, size in RESOLUTIONS:
            self.size_combo.addItem(title, size)
        self.size_combo.setCurrentIndex(2)
        self.auto_check = QCheckBox("自动拍照")
        self.crop_check = QCheckBox("裁剪并校正后保存")
        for widget in (QLabel("摄像头"), self.camera_combo, self.btn_probe, QLabel("分辨率"), self.size_combo, self.auto_check, self.crop_check):
            camera_row.addWidget(widget)
        camera_row.addStretch(1)
        root.addWidget(self.camera_box)

        self.folder_box = QWidget()
        folder_row = QHBoxLayout(self.folder_box)
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("选择扫描仪或手机保存图片的文件夹，新图片出现后会自动识别")
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(QLabel("文件夹"))
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(btn_browse)
        self.folder_box.hide()
        root.addWidget(self.folder_box)

        control_row = QHBoxLayout()
        self.btn_start = QPushButton("开始扫描")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(36)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop = QPushButton("结束并保存")
        self.btn_stop.setMinimumHeight(36)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        self.btn_shot = QPushButton("拍照并识别（空格）")
        self.btn_shot.setMinimumHeight(36)
        self.btn_shot.clicked.connect(self._on_shot)
        self.btn_shot.setEnabled(False)
        self.answer_picker = AnswerPicker(self.ctx)
        control_row.addWidget(self.answer_picker)
        control_row.addWidget(self.btn_start)
        control_row.addWidget(self.btn_shot)
        control_row.addWidget(self.btn_stop)
        control_row.addStretch(1)
        self.counter_label = QLabel("")
        control_row.addWidget(self.counter_label)
        root.addLayout(control_row)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._on_shot)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.hide()
        root.addWidget(self.message)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel("点击“开始扫描”后在此显示摄像头画面")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(360, 270)
        self.preview.setStyleSheet("background:#1f2937;color:#9ca3af;border-radius:6px;")
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.preview, 1)
        left_layout.addWidget(self.status_label)
        splitter.addWidget(left)

        right = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(self._on_row_selected)
        self.table.itemChanged.connect(self._on_cell_edited)
        right.addWidget(self.table)
        self.viewer = ImageViewer()
        right.addWidget(self.viewer)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

    # ---------- 状态与提示 ----------

    def show_message(self, text, error=False):
        self.message.setText(text)
        self.message.setStyleSheet(
            "color:#b42318;background:#fef3f2;padding:6px 10px;border-radius:4px;"
            if error
            else "color:#175cd3;background:#eff8ff;padding:6px 10px;border-radius:4px;"
        )
        self.message.show()

    def _update_mode(self):
        camera = self.radio_camera.isChecked()
        self.camera_box.setVisible(camera)
        self.folder_box.setVisible(not camera)
        self.preview.setVisible(camera)
        self.status_label.setVisible(camera)

    def _set_running(self, running: bool):
        self.running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.answer_picker.setEnabled(not running)
        self.radio_camera.setEnabled(not running)
        self.radio_folder.setEnabled(not running)
        self.camera_box.setEnabled(not running)
        self.folder_box.setEnabled(not running)
        self.btn_shot.setEnabled(False)

    def _on_project_changed(self):
        self.setEnabled(self.ctx.project is not None)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.probed and self.radio_camera.isChecked():
            self.probe_cameras()

    # ---------- 摄像头探测 ----------

    def probe_cameras(self):
        if self.probe_worker is not None and self.probe_worker.isRunning():
            return
        from app.camera import probe_cameras

        self.probed = True
        self.btn_probe.setEnabled(False)
        self.camera_combo.clear()
        self.camera_combo.addItem("正在查找摄像头…", None)
        self.probe_worker = Worker(probe_cameras)
        self.probe_worker.done.connect(self._on_probed)
        self.probe_worker.start()

    def _on_probed(self, cameras, error):
        self.btn_probe.setEnabled(True)
        self.camera_combo.clear()
        if error or not cameras:
            self.camera_combo.addItem("未找到摄像头", None)
            return
        for index, label in cameras:
            self.camera_combo.addItem(label, index)

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择监控的文件夹", self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    # ---------- 开始与结束 ----------

    def start(self):
        project = self.ctx.project
        if not project or self.running:
            return
        camera_mode = self.radio_camera.isChecked()
        if camera_mode and self.camera_combo.currentData() is None:
            self.show_message("没有可用的摄像头，请连接后点“刷新摄像头”。", error=True)
            return
        if not camera_mode and not Path(self.folder_edit.text().strip()).is_dir():
            self.show_message("请选择一个有效的监控文件夹。", error=True)
            return
        error = self.prepare()
        if error:
            QMessageBox.warning(self, "无法开始", error)
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(self.ctx.settings.get("app.output_root")) / project.name / stamp
        meta_dir = self.run_dir / "_run"
        meta_dir.mkdir(parents=True, exist_ok=True)
        snapshot = meta_dir / "project"
        snapshot.mkdir(exist_ok=True)
        import shutil

        shutil.copyfile(project.dir / "template.json", snapshot / "template.json")
        chosen_answer = self.answer_picker.selected_path()
        if chosen_answer:
            shutil.copyfile(chosen_answer, snapshot / "evaluation.json")
        config_path = meta_dir / "config.json"
        write_config(config_path, project.engine_config(self.ctx.settings))
        self.inbox = meta_dir / "inbox" if camera_mode else Path(self.folder_edit.text().strip())
        self.inbox.mkdir(parents=True, exist_ok=True)

        fields = project.text_fields
        self.text_fields = [f["name"] for f in fields]
        self.texts = {}
        self._text_note_shown = False
        self.reader.reset()
        engine_options = dict(self.answer_picker.engine_options())
        if fields:
            textfields_path = meta_dir / "textfields.json"
            project.write_text_fields(textfields_path)
            engine_options["textfields"] = textfields_path
        self.columns, self.rows, self.statuses = [], [], {}
        self.captured_count = 0
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.viewer.show_text("")
        self._update_counter()
        self._set_running(True)
        self.show_message("正在启动识别引擎…")
        self.job.start(
            "watch",
            project=project.dir,
            config=config_path,
            folder=self.inbox,
            output=self.run_dir,
            **engine_options,
        )

    def _on_engine_ready(self, columns: list):
        self.columns = columns
        headers = ["序号", "文件", "状态", "得分"] + self.text_fields + columns
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        if self.radio_camera.isChecked():
            self._start_camera()
        else:
            self.show_message(f"正在监控：{self.inbox}。新图片出现后会自动识别。")

    def _start_camera(self):
        from app.camera import CameraWorker

        project = self.ctx.project
        page_w, page_h = project.template["pageDimensions"]
        self.camera = CameraWorker(
            self.camera_combo.currentData(),
            self.inbox,
            size=self.size_combo.currentData(),
            auto=self.auto_check.isChecked(),
            crop=self.crop_check.isChecked(),
            aspect=page_w / page_h,
        )
        self.camera.frameReady.connect(self._on_frame)
        self.camera.statusChanged.connect(self.status_label.setText)
        self.camera.captured.connect(self._on_captured)
        self.camera.failed.connect(self._on_camera_failed)
        self.camera.start()
        self.btn_shot.setEnabled(True)
        mode = "自动拍照已开启：卡片入画并保持不动即会拍照。" if self.auto_check.isChecked() else "把答题卡放入画面，点击“拍照并识别”（或按空格）。"
        self.show_message("摄像头已就绪。" + mode)

    def _on_frame(self, image: QImage):
        pixmap = QPixmap.fromImage(image).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pixmap)

    def _on_shot(self):
        if self.running and self.camera is not None and self.btn_shot.isEnabled():
            self.camera.request_capture()

    def _on_captured(self, path: str):
        self.captured_count += 1
        self._update_counter()

    def _on_camera_failed(self, text: str):
        self.show_message(text, error=True)
        self.btn_shot.setEnabled(False)

    def _on_log(self, line: str):
        pass

    def _on_row(self, row: dict):
        self.rows.append(row)
        self.statuses[row["file"]] = row["status"]
        index = self.table.rowCount()
        self.table.insertRow(index)
        file_texts = self.texts.get(row["file"], {})
        values = (
            [str(len(self.rows)), row["file"], STATUS_TEXT.get(row["status"], row["status"]), row["score"]]
            + [file_texts.get(name, "") for name in self.text_fields]
            + row["answers"]
        )
        text_start, text_end = 4, 4 + len(self.text_fields)
        self._filling = True
        color = STATUS_COLOR.get(row["status"])
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if color:
                item.setBackground(QBrush(color))
            if not text_start <= column < text_end:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(index, column, item)
        self._filling = False
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.scrollToBottom()
        self._update_counter()

    def _on_crop(self, info: dict):
        if not self.reader.available():
            if not self._text_note_shown:
                self._text_note_shown = True
                self.show_message("已定义文字区域，但未在设置中配置大模型，文字没有识别。", error=True)
            return
        self.reader.enqueue(info["file"], info["field"], info["path"])

    def _on_text_ready(self, file: str, field: str, text: str):
        self.texts.setdefault(file, {})[field] = text
        if self.run_dir is not None:
            save_texts(self.run_dir, self.text_fields, self.texts)
        if field not in self.text_fields:
            return
        column = 4 + self.text_fields.index(field)
        for row_index, row in enumerate(self.rows):
            if row["file"] == file and row_index < self.table.rowCount():
                self._filling = True
                item = self.table.item(row_index, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row_index, column, item)
                item.setText(text)
                self._filling = False
                break

    def _on_cell_edited(self, item):
        if self._filling or not self.text_fields:
            return
        column, row_index = item.column() - 4, item.row()
        if not (0 <= column < len(self.text_fields) and 0 <= row_index < len(self.rows)):
            return
        file = self.rows[row_index]["file"]
        self.texts.setdefault(file, {})[self.text_fields[column]] = item.text().strip()
        if self.run_dir is not None:
            save_texts(self.run_dir, self.text_fields, self.texts)

    def _update_counter(self):
        abnormal = sum(1 for s in self.statuses.values() if s != "ok")
        parts = []
        if self.radio_camera.isChecked():
            parts.append(f"已拍 {self.captured_count} 张")
        parts.append(f"已识别 {len(self.rows)} 张")
        if abnormal:
            parts.append(f"异常 {abnormal} 张")
        self.counter_label.setText("，".join(parts))

    def _on_row_selected(self, *_):
        row_index = self.table.currentRow()
        if not (0 <= row_index < len(self.rows)) or self.run_dir is None:
            return
        marked = self.run_dir / "CheckedOMRs" / self.rows[row_index]["file"]
        if marked.exists():
            self.viewer.set_pixmap(QPixmap(str(marked)))
        else:
            self.viewer.show_text("没有标注图")

    def stop(self):
        """结束扫描：先停摄像头，等待已拍的图片全部识别完，再通知引擎退出并保存记录。"""
        if not self.running:
            return
        self.btn_stop.setEnabled(False)
        self.btn_shot.setEnabled(False)
        if self.camera is not None:
            self.camera.stop()
            self.camera.wait(3000)
            self.camera = None
        self._drain_elapsed = 0.0
        self.show_message("正在处理剩余图片…")
        self._drain_timer.start()

    def _pending_count(self) -> int:
        if self.inbox is None:
            return 0
        images = [p for p in self.inbox.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf")]
        done = {r["file"] for r in self.rows}
        return sum(1 for p in images if p.name not in done)

    def _check_drained(self):
        self._drain_elapsed += 0.3
        if (self._pending_count() > 0 or self.reader.pending() > 0) and self._drain_elapsed < DRAIN_TIMEOUT_SECONDS:
            return
        self._drain_timer.stop()
        (self.run_dir / "_run" / "stop").write_text("1")
        self._kill_timer.start(KILL_TIMEOUT_SECONDS * 1000)

    def _on_engine_finished(self, result: dict):
        self._kill_timer.stop()
        self._drain_timer.stop()
        was_running = self.running
        if self.camera is not None:
            self.camera.stop()
            self.camera.wait(3000)
            self.camera = None
        self._set_running(False)
        if not was_running or self.run_dir is None:
            return
        failed = not result.get("ok") and not result.get("cancelled")
        meta = {
            "project": self.ctx.project.name if self.ctx.project else "",
            "finished": datetime.now().isoformat(timespec="seconds"),
            "total": len(self.statuses),
            "statuses": self.statuses,
            "inputs": [r.get("input", "") for r in self.rows if r.get("input")],
            "ok": not failed,
        }
        if self.run_dir.exists():
            (self.run_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        if failed:
            self.show_message(f"识别引擎异常结束：{result.get('error', '未知错误')}", error=True)
        else:
            abnormal = sum(1 for s in self.statuses.values() if s != "ok")
            self.show_message(
                f"已保存：共 {len(self.statuses)} 张，异常 {abnormal} 张。可在“开始阅卷”页的历史记录中查看和导出。"
            )

    def shutdown(self):
        """程序退出前释放摄像头并结束引擎。"""
        if self.camera is not None:
            self.camera.stop()
            self.camera.wait(2000)
            self.camera = None
        if self.run_dir is not None and self.running:
            (self.run_dir / "_run").mkdir(parents=True, exist_ok=True)
            (self.run_dir / "_run" / "stop").write_text("1")
        self.job.cancel()
