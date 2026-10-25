"""版面模板页：在空白答题卡上框选字段块，并设置页面、预处理与合并列。"""
import re
from pathlib import Path
from typing import List, Optional

import json

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import ai_assist
from app.engine_client import EngineJob, temp_path, write_config
from app.layout_apply import blocks_from_detection
from app.project import (
    CUSTOM_TYPE,
    FIELD_TYPE_PRESETS,
    PREPROCESSORS,
    Block,
    default_preprocessor_options,
    expand_labels,
    make_label_text,
)
from app.ui.widgets import Worker
from app.ui.canvas import (
    BLOCK_COLORS,
    MODE_DRAW_BLOCK,
    MODE_MEASURE_BUBBLE,
    MODE_SELECT,
    SheetCanvas,
)
from app.ui.context import AppContext

MODEL_IMAGE_SIDE = 1024
IMAGE_FILTER = "答题卡图片 (*.jpg *.jpeg *.png *.pdf);;所有文件 (*.*)"

# 预处理参数规格：(键, 显示名, 类型, 最小值, 最大值)；pair 为两个整数组成的数组
PRE_FIELDS = {
    "CropPage": [("morphKernel", "形态学核（宽、高）", "pair", 1, 100)],
    "CropOnMarkers": [
        ("sheetToMarkerWidthRatio", "页面与标记点宽度比", "float", 1, 100),
        ("min_matching_threshold", "最低匹配度", "float", 0, 1),
        ("max_matching_variation", "最大匹配偏差", "float", 0, 1),
        ("apply_erode_subtract", "先做腐蚀相减", "bool", 0, 1),
    ],
    "Levels": [
        ("low", "黑场（0~1）", "float", 0, 1),
        ("high", "白场（0~1）", "float", 0, 1),
        ("gamma", "伽马", "float", 0.1, 5),
    ],
    "GaussianBlur": [
        ("kSize", "核大小（宽、高，奇数）", "pair", 1, 99),
        ("sigmaX", "标准差", "float", 0, 50),
    ],
    "MedianBlur": [("kSize", "核大小（奇数）", "int", 1, 99)],
    "FeatureBasedAlignment": [
        ("maxFeatures", "最大特征点数", "int", 10, 5000),
        ("goodMatchPercent", "优质匹配比例", "float", 0, 1),
        ("2d", "仅做二维变换", "bool", 0, 1),
    ],
}
PRE_DEFAULTS = {
    "apply_erode_subtract": True,
    "min_matching_threshold": 0.3,
    "max_matching_variation": 0.41,
    "goodMatchPercent": 0.15,
    "2d": False,
}
FILE_OPTION = {"CropOnMarkers": "relativePath", "FeatureBasedAlignment": "reference"}


class TemplatePage(QWidget):
    changed = Signal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.blocks: List[Block] = []
        self.current = -1
        self._updating = False
        self._pixmap: Optional[QPixmap] = None
        self._preview_job = EngineJob(self)
        self._preview_job.finished.connect(self._on_preview_finished)
        self._preview_pending = False
        self._preview_first = True
        self._check_job = EngineJob(self)
        self._check_job.finished.connect(self._on_check_finished)
        self._detect_job = EngineJob(self)
        self._detect_job.finished.connect(self._on_detect_finished)
        self._ai_worker: Optional[Worker] = None
        self._ai_timer: Optional[QTimer] = None
        self._ai_elapsed = 0
        self._ai_text = ""
        self._ai_limit = 0
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(700)
        self._preview_timer.timeout.connect(self.refresh_preview)
        self._build_ui()
        ctx.projectChanged.connect(self.reload)

    # ---------- 界面搭建 ----------

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 12)
        left_layout.addLayout(self._build_toolbar())
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.hide()
        left_layout.addWidget(self.message)
        self.canvas = SheetCanvas()
        self.canvas.set_dims_provider(self._bubble_dims)
        self.canvas.blockSelected.connect(self._on_canvas_selected)
        self.canvas.blockGeometryChanged.connect(self._on_canvas_geometry)
        self.canvas.rectDrawn.connect(self._on_rect_drawn)
        self.canvas.bubbleMeasured.connect(self._on_bubble_measured)
        self.canvas.nudged.connect(self._on_nudged)
        self.canvas.deletePressed.connect(self.delete_block)
        self.canvas.cursorMoved.connect(
            lambda x, y: self.coord_label.setText(f"坐标 {x}, {y}")
        )
        left_layout.addWidget(self.canvas, 1)
        self.empty_hint = QLabel("请先载入一张空白答题卡图片")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setObjectName("hint")
        left_layout.addWidget(self.empty_hint)
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_block_tab(), "字段块")
        self.tabs.addTab(self._build_page_tab(), "页面与预处理")
        self.tabs.addTab(self._build_check_tab(), "校验")
        self.tabs.setMinimumWidth(470)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([860, 500])

    def _build_toolbar(self):
        bar = QHBoxLayout()
        self.btn_load = QPushButton("载入空白答题卡…")
        self.btn_load.clicked.connect(self.load_reference)
        self.btn_refresh = QPushButton("刷新预览")
        self.btn_refresh.clicked.connect(self.refresh_preview)
        bar.addWidget(self.btn_load)
        bar.addWidget(self.btn_refresh)
        self.btn_detect = QPushButton("自动识别版面")
        self.btn_detect.setObjectName("primary")
        self.btn_detect.clicked.connect(self.auto_detect)
        self.use_ai = QCheckBox("大模型修正")
        bar.addWidget(self.btn_detect)
        bar.addWidget(self.use_ai)
        self._update_ai_option()
        bar.addSpacing(12)

        self.mode_buttons = {}
        for mode, text in [
            (MODE_SELECT, "选择 / 移动"),
            (MODE_DRAW_BLOCK, "框选字段块"),
            (MODE_MEASURE_BUBBLE, "量取气泡大小"),
        ]:
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            bar.addWidget(button)
            self.mode_buttons[mode] = button
        self.mode_buttons[MODE_SELECT].setChecked(True)
        bar.addStretch(1)

        self.coord_label = QLabel("")
        self.coord_label.setObjectName("hint")
        bar.addWidget(self.coord_label)
        for text, callback in [
            ("适应窗口", self.canvas_fit),
            ("100%", lambda: self.canvas.reset_zoom()),
            ("放大", lambda: self.canvas.zoom(1.25)),
            ("缩小", lambda: self.canvas.zoom(0.8)),
        ]:
            button = QToolButton()
            button.setText(text)
            button.clicked.connect(callback)
            bar.addWidget(button)
        return bar

    def _build_block_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.block_list = QListWidget()
        self.block_list.setMaximumHeight(150)
        self.block_list.currentRowChanged.connect(self._on_list_selected)
        layout.addWidget(self.block_list)

        row = QHBoxLayout()
        self.btn_copy = QPushButton("复制")
        self.btn_copy.clicked.connect(self.copy_block)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.clicked.connect(self.delete_block)
        hint = QLabel("在“框选字段块”模式下，沿首个气泡左上角到末个气泡右下角拖出外框。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(hint)

        self.block_form = QGroupBox("字段块属性")
        form = QFormLayout(self.block_form)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_name_edited)
        form.addRow("名称", self.name_edit)

        self.type_combo = QComboBox()
        for key, (title, _, _) in FIELD_TYPE_PRESETS.items():
            self.type_combo.addItem(title, key)
        self.type_combo.addItem("自定义", CUSTOM_TYPE)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("题型", self.type_combo)

        self.values_edit = QLineEdit()
        self.values_edit.setPlaceholderText("例如 A,B,C,D 或 T,F")
        self.values_edit.editingFinished.connect(self._on_values_edited)
        form.addRow("选项", self.values_edit)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("选项横向排列", "horizontal")
        self.direction_combo.addItem("选项纵向排列", "vertical")
        self.direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        form.addRow("方向", self.direction_combo)

        self.labels_edit = QLineEdit()
        self.labels_edit.setPlaceholderText("例如 q1..20")
        self.labels_edit.editingFinished.connect(self._on_labels_edited)
        form.addRow("字段标签", self.labels_edit)
        self.labels_info = QLabel("")
        self.labels_info.setObjectName("hint")
        form.addRow("", self.labels_info)

        gen = QHBoxLayout()
        self.gen_prefix = QLineEdit("q")
        self.gen_prefix.setMaximumWidth(60)
        self.gen_start = QSpinBox()
        self.gen_start.setRange(0, 9999)
        self.gen_start.setMinimumWidth(66)
        self.gen_start.setValue(1)
        self.gen_count = QSpinBox()
        self.gen_count.setRange(1, 999)
        self.gen_count.setMinimumWidth(66)
        self.gen_count.setValue(10)
        btn_gen = QPushButton("生成")
        btn_gen.setMinimumWidth(56)
        btn_gen.clicked.connect(self._on_generate_labels)
        for widget in (
            QLabel("前缀"),
            self.gen_prefix,
            QLabel("起"),
            self.gen_start,
            QLabel("数量"),
            self.gen_count,
            btn_gen,
        ):
            gen.addWidget(widget)
        form.addRow("按题号生成", gen)

        self.x_spin, self.y_spin, self.w_spin, self.h_spin = (self._geometry_spin() for _ in range(4))
        geo = QHBoxLayout()
        for label, spin in [("X", self.x_spin), ("Y", self.y_spin), ("宽", self.w_spin), ("高", self.h_spin)]:
            geo.addWidget(QLabel(label))
            geo.addWidget(spin)
        form.addRow("外框", geo)

        self.own_bubble = QCheckBox("独立大小")
        self.own_bubble.toggled.connect(self._on_own_bubble_toggled)
        self.bubble_w = self._bubble_spin()
        self.bubble_h = self._bubble_spin()
        own = QHBoxLayout()
        own.addWidget(self.own_bubble)
        own.addWidget(self.bubble_w)
        own.addWidget(QLabel("×"))
        own.addWidget(self.bubble_h)
        form.addRow("气泡", own)

        self.gap_info = QLabel("")
        self.gap_info.setObjectName("hint")
        self.gap_info.setWordWrap(True)
        form.addRow("", self.gap_info)
        layout.addWidget(self.block_form)
        layout.addStretch(1)
        self.block_form.setEnabled(False)
        return tab

    def _geometry_spin(self):
        spin = QSpinBox()
        spin.setRange(0, 20000)
        spin.setMinimumWidth(66)
        spin.valueChanged.connect(self._on_geometry_edited)
        return spin

    def _bubble_spin(self):
        spin = QSpinBox()
        spin.setRange(2, 500)
        spin.setValue(20)
        spin.setMinimumWidth(66)
        spin.valueChanged.connect(self._on_own_bubble_edited)
        return spin

    def _build_page_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        page_box = QGroupBox("页面与气泡")
        form = QFormLayout(page_box)
        self.page_w, self.page_h = QSpinBox(), QSpinBox()
        for spin in (self.page_w, self.page_h):
            spin.setRange(100, 20000)
            spin.valueChanged.connect(self._on_page_edited)
        row = QHBoxLayout()
        row.addWidget(self.page_w)
        row.addWidget(QLabel("×"))
        row.addWidget(self.page_h)
        form.addRow("页面尺寸", row)

        self.default_bw, self.default_bh = QSpinBox(), QSpinBox()
        for spin in (self.default_bw, self.default_bh):
            spin.setRange(2, 500)
            spin.valueChanged.connect(self._on_default_bubble_edited)
        row = QHBoxLayout()
        row.addWidget(self.default_bw)
        row.addWidget(QLabel("×"))
        row.addWidget(self.default_bh)
        form.addRow("气泡大小", row)

        self.own_processing = QCheckBox("本方案使用独立处理尺寸")
        self.own_processing.toggled.connect(self._on_processing_edited)
        self.proc_w, self.proc_h = QSpinBox(), QSpinBox()
        for spin in (self.proc_w, self.proc_h):
            spin.setRange(200, 4000)
            spin.valueChanged.connect(self._on_processing_edited)
        row = QHBoxLayout()
        row.addWidget(self.proc_w)
        row.addWidget(QLabel("×"))
        row.addWidget(self.proc_h)
        form.addRow(self.own_processing, row)
        note = QLabel("未勾选时使用“设置”中的全局处理尺寸。")
        note.setObjectName("hint")
        form.addRow("", note)
        layout.addWidget(page_box)

        pre_box = QGroupBox("预处理（自上而下依次执行）")
        pre_layout = QVBoxLayout(pre_box)
        self.pre_list = QListWidget()
        self.pre_list.setMaximumHeight(150)
        self.pre_list.itemChanged.connect(self._on_pre_toggled)
        self.pre_list.currentRowChanged.connect(self._on_pre_selected)
        pre_layout.addWidget(self.pre_list)
        row = QHBoxLayout()
        up = QPushButton("上移")
        up.clicked.connect(lambda: self._move_pre(-1))
        down = QPushButton("下移")
        down.clicked.connect(lambda: self._move_pre(1))
        row.addWidget(up)
        row.addWidget(down)
        row.addStretch(1)
        pre_layout.addLayout(row)
        self.pre_stack = QStackedWidget()
        self.pre_empty = QLabel("选中一项预处理以编辑参数")
        self.pre_empty.setObjectName("hint")
        self.pre_stack.addWidget(self.pre_empty)
        self.pre_panels = {}
        for name in PREPROCESSORS:
            panel = QWidget()
            panel.setLayout(QFormLayout())
            self.pre_stack.addWidget(panel)
            self.pre_panels[name] = panel
        pre_layout.addWidget(self.pre_stack)
        layout.addWidget(pre_box)

        label_box = QGroupBox("合并列（将多个字段拼成一列输出，例如准考号）")
        label_layout = QVBoxLayout(label_box)
        self.custom_table = QTableWidget(0, 2)
        self.custom_table.setHorizontalHeaderLabels(["列名", "包含的字段标签"])
        self.custom_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.custom_table.setMaximumHeight(130)
        self.custom_table.itemChanged.connect(self._on_custom_edited)
        label_layout.addWidget(self.custom_table)
        row = QHBoxLayout()
        add = QPushButton("添加")
        add.clicked.connect(self._add_custom_row)
        remove = QPushButton("删除选中")
        remove.clicked.connect(self._remove_custom_row)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        label_layout.addLayout(row)
        layout.addWidget(label_box)
        layout.addStretch(1)
        return tab

    def _build_check_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        info = QLabel("按引擎规则检查字段标签是否重叠、字段块是否超出页面、合并列是否引用了不存在的标签。")
        info.setWordWrap(True)
        info.setObjectName("hint")
        layout.addWidget(info)
        self.btn_check = QPushButton("校验模板")
        self.btn_check.clicked.connect(self.check_template)
        layout.addWidget(self.btn_check)
        self.check_output = QPlainTextEdit()
        self.check_output.setReadOnly(True)
        layout.addWidget(self.check_output, 1)
        return tab

    # ---------- 方案加载与保存 ----------

    @property
    def project(self):
        return self.ctx.project

    def _bubble_dims(self):
        if self.project:
            return self.project.default_dims
        return (20, 20)

    def reload(self):
        self._updating = True
        project = self.project
        self.blocks = project.blocks() if project else []
        self.current = -1
        self._pixmap = None
        self._preview_first = True
        self.canvas.set_image(None, 0, 0)
        self.setEnabled(project is not None)
        if project:
            width, height = project.template["pageDimensions"]
            self.page_w.setValue(width)
            self.page_h.setValue(height)
            bw, bh = project.default_dims
            self.default_bw.setValue(int(bw))
            self.default_bh.setValue(int(bh))
            processing = project.processing
            self.own_processing.setChecked(processing is not None)
            engine_dims = self.ctx.settings
            self.proc_w.setValue(
                processing[0] if processing else int(engine_dims.get("dimensions.processing_width"))
            )
            self.proc_h.setValue(
                processing[1] if processing else int(engine_dims.get("dimensions.processing_height"))
            )
            self.canvas.set_image(None, width, height)
            self._reload_pre_list()
            self._reload_custom_table()
        self._updating = False
        self._refresh_block_list()
        self._show_empty_hint()
        self.hide_message()
        self.check_output.clear()
        if project and project.reference_path and project.reference_path.exists():
            self.refresh_preview()

    def on_settings_changed(self):
        """全局处理尺寸变化后，同步未使用独立尺寸的方案并刷新预览。"""
        self._update_ai_option()
        if not self.project or self.own_processing.isChecked():
            return
        settings = self.ctx.settings
        self._updating = True
        self.proc_w.setValue(int(settings.get("dimensions.processing_width")))
        self.proc_h.setValue(int(settings.get("dimensions.processing_height")))
        self._updating = False
        self.schedule_preview()

    def _show_empty_hint(self):
        has_reference = bool(self.project and self.project.reference_path)
        self.empty_hint.setVisible(not has_reference)

    def commit(self):
        """将编辑器状态写回方案对象（未写盘）。"""
        if not self.project or self._updating:
            return
        self.project.set_blocks(self.blocks)
        self.changed.emit()

    def save(self):
        if self.project:
            self.project.save()

    def set_mode(self, mode):
        if mode != MODE_SELECT and not self.canvas.has_image():
            self.show_message("请先载入空白答题卡图片，并等待预览生成。")
            mode = MODE_SELECT
        for key, button in self.mode_buttons.items():
            button.setChecked(key == mode)
        self.canvas.set_mode(mode)

    def canvas_fit(self):
        self.canvas.fit()

    # ---------- 提示信息 ----------

    def show_message(self, text, error=True):
        self.message.setText(text)
        self.message.setStyleSheet(
            "color:#b42318;background:#fef3f2;padding:6px 10px;border-radius:4px;"
            if error
            else "color:#175cd3;background:#eff8ff;padding:6px 10px;border-radius:4px;"
        )
        self.message.show()

    def hide_message(self):
        self.message.hide()

    # ---------- 预览 ----------

    def load_reference(self):
        if not self.project:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择空白答题卡", "", IMAGE_FILTER)
        if not path:
            return
        self.project.set_reference(path)
        self._preview_first = True
        self._show_empty_hint()
        self.save()
        self.refresh_preview()

    def schedule_preview(self):
        if self.project and self.project.reference_path:
            self._preview_timer.start()

    def refresh_preview(self):
        project = self.project
        if not project or not project.reference_path or not project.reference_path.exists():
            return
        if self._preview_job.running():
            self._preview_pending = True
            return
        self.save()
        config_path = temp_path("preview_config.json")
        write_config(config_path, project.engine_config(self.ctx.settings))
        self._preview_out = temp_path("preview.png")
        self._preview_out.unlink(missing_ok=True)
        self.show_message("正在生成预览…", error=False)
        self.btn_refresh.setEnabled(False)
        self._preview_job.start(
            "preview",
            project=project.dir,
            config=config_path,
            image=project.reference_path,
            out=self._preview_out,
        )

    def _on_preview_finished(self, result):
        self.btn_refresh.setEnabled(True)
        if self._preview_pending:
            self._preview_pending = False
            self.refresh_preview()
            return
        if not result.get("ok"):
            message = f"预处理失败：{result.get('error', '未知错误')}"
            raw = QPixmap(str(self.project.reference_path)) if self.project else QPixmap()
            if not raw.isNull():
                width, height = self.project.template["pageDimensions"]
                self._pixmap = raw
                self.canvas.set_image(raw, width, height)
                self.canvas.set_blocks(self.blocks, self.current)
                if self._preview_first:
                    self.canvas.fit()
                    self._preview_first = False
                message += "。当前显示未经预处理的原图，字段块位置仅供参考。"
            self.show_message(message)
            return
        pixmap = QPixmap(str(self._preview_out))
        if pixmap.isNull():
            self.show_message("预览失败：无法读取生成的预览图")
            return
        self._pixmap = pixmap
        width, height = result["width"], result["height"]
        self.canvas.set_image(pixmap, width, height)
        self.canvas.set_blocks(self.blocks, self.current)
        if self._preview_first:
            self.canvas.fit()
            self._preview_first = False
        self.hide_message()

    # ---------- 字段块列表 ----------

    def _refresh_block_list(self):
        self._updating = True
        self.block_list.clear()
        for block in self.blocks:
            self.block_list.addItem(block.name)
        if 0 <= self.current < len(self.blocks):
            self.block_list.setCurrentRow(self.current)
        self._updating = False
        self.canvas.set_blocks(self.blocks, self.current)
        self._fill_block_form()

    def _fill_block_form(self):
        self._updating = True
        enabled = 0 <= self.current < len(self.blocks)
        self.block_form.setEnabled(enabled)
        self.btn_copy.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)
        if enabled:
            block = self.blocks[self.current]
            self.name_edit.setText(block.name)
            index = self.type_combo.findData(block.field_type)
            self.type_combo.setCurrentIndex(index if index >= 0 else self.type_combo.count() - 1)
            custom = block.field_type == CUSTOM_TYPE
            self.values_edit.setText(",".join(block.values))
            self.values_edit.setEnabled(custom)
            self.direction_combo.setCurrentIndex(self.direction_combo.findData(block.direction))
            self.direction_combo.setEnabled(custom)
            self.labels_edit.setText(block.labels)
            self._update_label_info(block)
            self.x_spin.setValue(int(round(block.x)))
            self.y_spin.setValue(int(round(block.y)))
            self.w_spin.setValue(int(round(block.w)))
            self.h_spin.setValue(int(round(block.h)))
            own = block.bubble_dims is not None
            self.own_bubble.setChecked(own)
            self.bubble_w.setEnabled(own)
            self.bubble_h.setEnabled(own)
            dims = block.bubble_dims or self._bubble_dims()
            self.bubble_w.setValue(int(dims[0]))
            self.bubble_h.setValue(int(dims[1]))
            self._update_gap_info(block)
        self._updating = False

    def _update_label_info(self, block):
        try:
            labels = expand_labels(block.label_items)
            self.labels_info.setStyleSheet("")
            self.labels_info.setText(f"共 {len(labels)} 个字段，每个字段 {len(block.values)} 个选项")
        except ValueError as error:
            self.labels_info.setStyleSheet("color:#b42318;")
            self.labels_info.setText(str(error))

    def _update_gap_info(self, block):
        bubbles_gap, labels_gap = block.gaps(self._bubble_dims())
        bw, bh = block.bubble_dims or self._bubble_dims()
        text = f"选项间距 {bubbles_gap:.1f} px，字段间距 {labels_gap:.1f} px"
        step = bw if block.direction == "horizontal" else bh
        if len(block.values) > 1 and bubbles_gap < step:
            text += "（选项间距小于气泡尺寸，气泡会重叠，请检查外框或数量）"
        self.gap_info.setText(text)

    def _on_list_selected(self, row):
        if self._updating:
            return
        self.current = row
        self.canvas.select_index(row)
        self._fill_block_form()

    def _on_canvas_selected(self, index):
        self.current = index
        self._updating = True
        self.block_list.setCurrentRow(index)
        self._updating = False
        self._fill_block_form()

    def _on_canvas_geometry(self, index):
        if index != self.current or not (0 <= index < len(self.blocks)):
            return
        block = self.blocks[index]
        self._updating = True
        self.x_spin.setValue(int(round(block.x)))
        self.y_spin.setValue(int(round(block.y)))
        self.w_spin.setValue(int(round(block.w)))
        self.h_spin.setValue(int(round(block.h)))
        self._updating = False
        self._update_gap_info(block)
        self.commit()

    def _on_rect_drawn(self, rect: QRectF):
        number = self._next_question_number()
        count = 10
        block = Block(
            name=self._unique_name("字段块"),
            x=rect.x(),
            y=rect.y(),
            w=rect.width(),
            h=rect.height(),
        )
        block.labels = make_label_text("q", number, count)
        block.set_type("QTYPE_MCQ4")
        self.blocks.append(block)
        self.current = len(self.blocks) - 1
        self.gen_start.setValue(number)
        self.commit()
        self._refresh_block_list()
        self.set_mode(MODE_SELECT)

    def _on_bubble_measured(self, rect: QRectF):
        width, height = int(round(rect.width())), int(round(rect.height()))
        self._updating = True
        self.default_bw.setValue(width)
        self.default_bh.setValue(height)
        self._updating = False
        self._on_default_bubble_edited()
        self.set_mode(MODE_SELECT)

    def _unique_name(self, base):
        names = {b.name for b in self.blocks}
        index = 1
        while f"{base}{index}" in names:
            index += 1
        return f"{base}{index}"

    def _next_question_number(self):
        highest = 0
        for block in self.blocks:
            try:
                labels = expand_labels(block.label_items)
            except ValueError:
                continue
            for label in labels:
                match = re.fullmatch(r"q(\d+)", label)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1

    def copy_block(self):
        if not (0 <= self.current < len(self.blocks)):
            return
        source = self.blocks[self.current]
        clone = Block(**{**source.__dict__})
        clone.values = list(source.values)
        clone.extra = dict(source.extra)
        clone.name = self._unique_name("字段块")
        clone.x += 20
        clone.y += 20
        number = self._next_question_number()
        clone.labels = make_label_text("q", number, max(source.label_count(), 1))
        self.blocks.append(clone)
        self.current = len(self.blocks) - 1
        self.commit()
        self._refresh_block_list()

    def delete_block(self):
        if not (0 <= self.current < len(self.blocks)):
            return
        del self.blocks[self.current]
        self.current = min(self.current, len(self.blocks) - 1)
        self.commit()
        self._refresh_block_list()

    # ---------- 字段块属性编辑 ----------

    def _selected(self) -> Optional[Block]:
        if self._updating or not (0 <= self.current < len(self.blocks)):
            return None
        return self.blocks[self.current]

    def _after_block_edit(self, block, refill=False):
        self._update_gap_info(block)
        self._update_label_info(block)
        self.canvas.refresh_blocks()
        self.commit()
        if refill:
            self._fill_block_form()

    def _on_name_edited(self):
        block = self._selected()
        if not block:
            return
        name = self.name_edit.text().strip()
        if not name or name == block.name:
            self.name_edit.setText(block.name)
            return
        if any(b.name == name for b in self.blocks):
            QMessageBox.warning(self, "名称重复", f"已存在名为“{name}”的字段块")
            self.name_edit.setText(block.name)
            return
        block.name = name
        self._updating = True
        self.block_list.item(self.current).setText(name)
        self._updating = False
        self._after_block_edit(block)

    def _on_type_changed(self):
        block = self._selected()
        if not block:
            return
        block.set_type(self.type_combo.currentData())
        self._after_block_edit(block, refill=True)

    def _on_values_edited(self):
        block = self._selected()
        if not block or block.field_type != CUSTOM_TYPE:
            return
        values = [v.strip() for v in re.split(r"[,，]", self.values_edit.text()) if v.strip()]
        if values:
            block.values = values
        self._after_block_edit(block, refill=True)

    def _on_direction_changed(self):
        block = self._selected()
        if not block or block.field_type != CUSTOM_TYPE:
            return
        block.direction = self.direction_combo.currentData()
        self._after_block_edit(block)

    def _on_labels_edited(self):
        block = self._selected()
        if not block:
            return
        text = self.labels_edit.text().strip()
        if text == block.labels:
            return
        block.labels = text
        self._after_block_edit(block)

    def _on_generate_labels(self):
        block = self._selected()
        if not block:
            return
        prefix = self.gen_prefix.text().strip() or "q"
        block.labels = make_label_text(prefix, self.gen_start.value(), self.gen_count.value())
        self._after_block_edit(block, refill=True)

    def _on_geometry_edited(self):
        block = self._selected()
        if not block:
            return
        block.x, block.y = self.x_spin.value(), self.y_spin.value()
        block.w, block.h = max(self.w_spin.value(), 1), max(self.h_spin.value(), 1)
        self._after_block_edit(block)

    def _on_own_bubble_toggled(self, checked):
        block = self._selected()
        if not block:
            return
        self.bubble_w.setEnabled(checked)
        self.bubble_h.setEnabled(checked)
        block.bubble_dims = (self.bubble_w.value(), self.bubble_h.value()) if checked else None
        self._after_block_edit(block)

    def _on_own_bubble_edited(self):
        block = self._selected()
        if not block or not self.own_bubble.isChecked():
            return
        block.bubble_dims = (self.bubble_w.value(), self.bubble_h.value())
        self._after_block_edit(block)

    # ---------- 页面与气泡 ----------

    def _on_page_edited(self):
        if self._updating or not self.project:
            return
        width, height = self.page_w.value(), self.page_h.value()
        self.project.template["pageDimensions"] = [width, height]
        self.canvas.set_image(self._pixmap, width, height)
        self.canvas.set_blocks(self.blocks, self.current)
        self.changed.emit()

    def _on_default_bubble_edited(self):
        if self._updating or not self.project:
            return
        self.project.template["bubbleDimensions"] = [
            self.default_bw.value(),
            self.default_bh.value(),
        ]
        self.commit()
        self.canvas.refresh_blocks()
        if 0 <= self.current < len(self.blocks):
            self._fill_block_form()

    def _on_processing_edited(self):
        if self._updating or not self.project:
            return
        own = self.own_processing.isChecked()
        self.proc_w.setEnabled(own)
        self.proc_h.setEnabled(own)
        if own:
            self.project.meta["processing"] = [self.proc_w.value(), self.proc_h.value()]
        else:
            self.project.meta.pop("processing", None)
        self.changed.emit()
        self.schedule_preview()

    # ---------- 预处理 ----------

    def _reload_pre_list(self):
        self._updating = True
        self.pre_list.clear()
        configured = self.project.template.get("preProcessors", [])
        order = [p["name"] for p in configured]
        order += [name for name in PREPROCESSORS if name not in order]
        options = {p["name"]: p.get("options", {}) for p in configured}
        for name in order:
            item = QListWidgetItem(PREPROCESSORS[name])
            item.setData(Qt.UserRole, name)
            defaults = default_preprocessor_options(name)
            if name == "FeatureBasedAlignment":
                defaults["reference"] = self.project.meta.get("reference", "reference.jpg")
            item.setData(Qt.UserRole + 1, options.get(name) or defaults)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in options else Qt.Unchecked)
            self.pre_list.addItem(item)
        self._updating = False
        self.pre_stack.setCurrentIndex(0)

    def _commit_pre(self):
        if self._updating or not self.project:
            return
        processors = []
        for row in range(self.pre_list.count()):
            item = self.pre_list.item(row)
            if item.checkState() == Qt.Checked:
                processors.append(
                    {"name": item.data(Qt.UserRole), "options": item.data(Qt.UserRole + 1)}
                )
        self.project.template["preProcessors"] = processors
        self.changed.emit()
        self.schedule_preview()

    def _on_pre_toggled(self, item):
        if self._updating:
            return
        name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked and name in FILE_OPTION:
            options = item.data(Qt.UserRole + 1)
            key = FILE_OPTION[name]
            target = self.project.dir / str(options.get(key, ""))
            if not target.is_file():
                if not self._choose_option_file(item):
                    self._updating = True
                    item.setCheckState(Qt.Unchecked)
                    self._updating = False
                    return
        self._commit_pre()

    def _choose_option_file(self, item) -> bool:
        name = item.data(Qt.UserRole)
        title = "选择标记点图片" if name == "CropOnMarkers" else "选择对齐参考图"
        path, _ = QFileDialog.getOpenFileName(self, title, "", "图片 (*.jpg *.jpeg *.png)")
        if not path:
            return False
        options = item.data(Qt.UserRole + 1)
        key = FILE_OPTION[name]
        if name == "CropOnMarkers":
            options[key] = self.project.set_marker(path)
        else:
            options[key] = self.project.set_aux_image(path, "align_reference")
        item.setData(Qt.UserRole + 1, options)
        return True

    def _on_pre_selected(self, row):
        if self._updating or row < 0:
            self.pre_stack.setCurrentIndex(0)
            return
        item = self.pre_list.item(row)
        name = item.data(Qt.UserRole)
        panel = self.pre_panels[name]
        self._fill_pre_panel(panel, item, name)
        self.pre_stack.setCurrentWidget(panel)

    def _fill_pre_panel(self, panel, item, name):
        form: QFormLayout = panel.layout()
        while form.rowCount():
            form.removeRow(0)
        options = item.data(Qt.UserRole + 1)

        def update(key, value):
            options[key] = value
            item.setData(Qt.UserRole + 1, options)
            self._commit_pre()

        if name in FILE_OPTION:
            key = FILE_OPTION[name]
            row = QHBoxLayout()
            label = QLabel(str(options.get(key, "未选择")))
            button = QPushButton("更换…")

            def change():
                if self._choose_option_file(item):
                    label.setText(str(item.data(Qt.UserRole + 1).get(key)))
                    self._commit_pre()

            button.clicked.connect(change)
            row.addWidget(label, 1)
            row.addWidget(button)
            form.addRow("标记点图片" if name == "CropOnMarkers" else "参考图", row)

        for key, title, kind, low, high in PRE_FIELDS[name]:
            value = options.get(key, PRE_DEFAULTS.get(key))
            if kind == "bool":
                box = QCheckBox()
                box.setChecked(bool(value))
                box.toggled.connect(lambda v, k=key: update(k, v))
                form.addRow(title, box)
            elif kind == "float":
                spin = QDoubleSpinBox()
                spin.setRange(low, high)
                spin.setDecimals(2)
                spin.setSingleStep(0.05 if high <= 1 else 1)
                spin.setValue(float(value if value is not None else low))
                spin.valueChanged.connect(lambda v, k=key: update(k, v))
                form.addRow(title, spin)
            elif kind == "int":
                spin = QSpinBox()
                spin.setRange(int(low), int(high))
                spin.setValue(int(value if value is not None else low))
                spin.valueChanged.connect(lambda v, k=key: update(k, v))
                form.addRow(title, spin)
            else:
                first, second = QSpinBox(), QSpinBox()
                for spin in (first, second):
                    spin.setRange(int(low), int(high))
                pair = list(value or [low, low])
                first.setValue(int(pair[0]))
                second.setValue(int(pair[1]))

                def pair_changed(_=None, k=key, a=first, b=second):
                    update(k, [a.value(), b.value()])

                first.valueChanged.connect(pair_changed)
                second.valueChanged.connect(pair_changed)
                row = QHBoxLayout()
                row.addWidget(first)
                row.addWidget(second)
                form.addRow(title, row)

    def _move_pre(self, delta):
        row = self.pre_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.pre_list.count()):
            return
        self._updating = True
        item = self.pre_list.takeItem(row)
        self.pre_list.insertItem(target, item)
        self.pre_list.setCurrentRow(target)
        self._updating = False
        self._commit_pre()

    # ---------- 合并列 ----------

    def _reload_custom_table(self):
        self._updating = True
        self.custom_table.setRowCount(0)
        for name, labels in self.project.template.get("customLabels", {}).items():
            row = self.custom_table.rowCount()
            self.custom_table.insertRow(row)
            self.custom_table.setItem(row, 0, QTableWidgetItem(name))
            self.custom_table.setItem(row, 1, QTableWidgetItem(",".join(labels)))
        self._updating = False

    def _add_custom_row(self):
        row = self.custom_table.rowCount()
        self._updating = True
        self.custom_table.insertRow(row)
        self.custom_table.setItem(row, 0, QTableWidgetItem(f"列{row + 1}"))
        self.custom_table.setItem(row, 1, QTableWidgetItem(""))
        self._updating = False
        self._on_custom_edited()

    def _remove_custom_row(self):
        row = self.custom_table.currentRow()
        if row >= 0:
            self.custom_table.removeRow(row)
            self._on_custom_edited()

    def _on_custom_edited(self, _item=None):
        if self._updating or not self.project:
            return
        labels = {}
        for row in range(self.custom_table.rowCount()):
            name_item, labels_item = self.custom_table.item(row, 0), self.custom_table.item(row, 1)
            name = name_item.text().strip() if name_item else ""
            parts = [p.strip() for p in re.split(r"[,，]", labels_item.text() if labels_item else "") if p.strip()]
            if name and parts:
                labels[name] = parts
        self.project.template["customLabels"] = labels
        self.changed.emit()

    # ---------- 自动识别版面 ----------

    def _update_ai_option(self):
        ready = ai_assist.configured(self.ctx.settings)
        self.use_ai.setEnabled(ready)
        self.use_ai.setText("大模型修正" if ready else "大模型修正（设置中未配置）")
        if not ready:
            self.use_ai.setChecked(False)

    def auto_detect(self):
        project = self.project
        if not project:
            return
        if not project.reference_path or not project.reference_path.exists():
            self.show_message("请先载入空白答题卡图片。")
            return
        if self._detect_job.running() or (self._ai_worker and self._ai_worker.isRunning()):
            return
        self.save()
        config_path = temp_path("detect_config.json")
        write_config(config_path, project.engine_config(self.ctx.settings))
        self._detect_out = temp_path("detect.json")
        self._detect_out.unlink(missing_ok=True)
        self.btn_detect.setEnabled(False)
        self.show_message("正在识别版面…", error=False)
        self._detect_job.start(
            "detect",
            project=project.dir,
            config=config_path,
            image=project.reference_path,
            out=self._detect_out,
        )

    def _on_detect_finished(self, result):
        if not result.get("ok"):
            self.btn_detect.setEnabled(True)
            self.show_message(f"识别版面失败：{result.get('error', '未知错误')}")
            return
        try:
            detection = json.loads(self._detect_out.read_text("utf-8"))
        except (OSError, ValueError):
            self.btn_detect.setEnabled(True)
            self.show_message("识别版面失败：无法读取识别结果")
            return
        if not detection["blocks"]:
            self.btn_detect.setEnabled(True)
            self.show_message(
                "没有识别到规整的气泡网格。请确认预览图清晰、预处理正确，或改用“框选字段块”手动标注。"
            )
            return
        if self.use_ai.isChecked() and ai_assist.configured(self.ctx.settings):
            settings = self.ctx.settings
            self._start_ai_timer("大模型正在判读版面", int(settings.get("ai.timeout")))
            png = self._overlay_image(detection)
            self._ai_detection = detection
            self._ai_worker = Worker(lambda: ai_assist.refine(settings, png, detection))
            self._ai_worker.done.connect(self._on_ai_done)
            self._ai_worker.start()
            return
        self._apply_detection(detection)

    def _start_ai_timer(self, text, limit):
        """每秒刷新一次提示，显示大模型已等待的秒数。"""
        self._ai_elapsed = 0
        if self._ai_timer is None:
            self._ai_timer = QTimer(self)
            self._ai_timer.setInterval(1000)
            self._ai_timer.timeout.connect(self._tick_ai_timer)
        self._ai_text, self._ai_limit = text, limit
        self._tick_ai_timer(first=True)
        self._ai_timer.start()

    def _tick_ai_timer(self, first=False):
        if not first:
            self._ai_elapsed += 1
        self.show_message(
            f"程序检测完成，{self._ai_text}…已用 {self._ai_elapsed} 秒（最长等待 {self._ai_limit} 秒）", error=False
        )

    def _on_ai_done(self, refined, error):
        if self._ai_timer is not None:
            self._ai_timer.stop()
        fallback = ""
        if error or refined is None:
            refined = self._ai_detection
            fallback = f"大模型调用失败，已改用程序检测结果（{error}）。"
        self._apply_detection(refined, note=fallback)

    def _overlay_image(self, detection) -> bytes:
        """生成带编号方框的答题卡图，供大模型对照判读。"""
        page_w, page_h = self.canvas.page_size()
        if self._pixmap is not None and not self._pixmap.isNull():
            image = self._pixmap.scaled(page_w, page_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:
            image = QPixmap(page_w, page_h)
            image.fill(Qt.white)
        painter = QPainter(image)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(16, page_h // 36))
        font.setBold(True)
        painter.setFont(font)
        for index, block in enumerate(detection["blocks"], start=1):
            color = QColor(BLOCK_COLORS[(index - 1) % len(BLOCK_COLORS)])
            painter.setPen(QPen(color, max(2, page_w // 400)))
            painter.drawRect(block["x"], block["y"], block["w"], block["h"])
            painter.drawText(block["x"] + 4, block["y"] - 4, str(index))
        painter.end()
        longest = max(page_w, page_h)
        if longest > MODEL_IMAGE_SIDE:
            image = image.scaled(
                int(page_w * MODEL_IMAGE_SIDE / longest),
                int(page_h * MODEL_IMAGE_SIDE / longest),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "JPG", 80)
        return bytes(data)

    def _apply_detection(self, detection, note=""):
        self.btn_detect.setEnabled(True)
        project = self.project
        if not project:
            return
        replace = True
        if self.blocks:
            box = QMessageBox(
                QMessageBox.Question,
                "自动识别版面",
                f"识别到 {len(detection['blocks'])} 个气泡网格块，当前已有 {len(self.blocks)} 个字段块。",
                parent=self,
            )
            replace_button = box.addButton("替换现有字段块", QMessageBox.AcceptRole)
            append_button = box.addButton("追加到现有字段块", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (replace_button, append_button):
                self.hide_message()
                return
            replace = clicked is replace_button

        start = 1 if replace else self._next_question_number()
        taken = set() if replace else {b.name for b in self.blocks}
        new_blocks, bubble, merges = blocks_from_detection(detection, taken, start)
        if not new_blocks:
            self.show_message("没有可用的字段块。")
            return
        first_new = 0 if replace else len(self.blocks)
        self.blocks = new_blocks if replace else self.blocks + new_blocks
        custom = {} if replace else dict(project.template.get("customLabels", {}))
        for name, labels in merges.items():
            custom.setdefault(name, labels)
        project.template["customLabels"] = custom
        if bubble and (replace or len(self.blocks) == len(new_blocks)):
            project.template["bubbleDimensions"] = [int(bubble[0]), int(bubble[1])]
            self._updating = True
            self.default_bw.setValue(int(bubble[0]))
            self.default_bh.setValue(int(bubble[1]))
            self._updating = False
        self.current = first_new
        self._reload_custom_table()
        self.commit()
        self._refresh_block_list()
        message = f"已生成 {len(new_blocks)} 个字段块，请对照底图检查并微调；准考号、题号可在右侧修改。"
        self.show_message((note + message) if note else message, error=bool(note))

    # ---------- 校验 ----------

    def check_template(self):
        if not self.project:
            return
        self.save()
        if self._check_job.running():
            return
        config_path = temp_path("check_config.json")
        write_config(config_path, self.project.engine_config(self.ctx.settings))
        self.check_output.setPlainText("正在校验…")
        self.btn_check.setEnabled(False)
        self._check_job.start("check", project=self.project.dir, config=config_path)
        self._check_log: List[str] = []
        self._check_job.logLine.connect(self._collect_check_log)

    def _collect_check_log(self, line):
        self._check_log.append(line)

    def _on_check_finished(self, result):
        self.btn_check.setEnabled(True)
        try:
            self._check_job.logLine.disconnect(self._collect_check_log)
        except (RuntimeError, TypeError):
            pass
        if result.get("ok"):
            columns = "、".join(result["columns"])
            self.check_output.setPlainText(
                f"校验通过：共 {result['fields']} 个字段。\n输出列：{columns}"
            )
        else:
            detail = [line for line in self._check_log if "critical" in line.lower() or "Exception" in line or "错误" in line]
            tail = "\n".join(self._check_log[-6:])
            self.check_output.setPlainText(
                f"校验未通过：{result.get('error', '未知错误')}\n\n{tail if not detail else chr(10).join(detail)}"
            )

    # ---------- 键盘微调 ----------

    def _on_nudged(self, dx, dy):
        block = self._selected()
        if not block:
            return
        block.x += dx
        block.y += dy
        self._fill_block_form()
        self.canvas.refresh_blocks()
        self.commit()
