"""答案与评分页：录入标准答案和分值（可从答案卡、答案图导入，或用自然语言描述评分规则），生成 evaluation.json。"""
import re
import shutil
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QInputDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import ai_assist
from app.engine_client import EngineJob, temp_path, write_config
from app.project import (
    AnswerRow,
    Marking,
    build_evaluation,
    expand_label_item,
    has_partial,
    parse_score,
    read_evaluation,
)
from app.scoring_rules import apply_rules, fill_answers
from app.ui.context import AppContext
from app.ui.run_page import read_run_results
from app.ui.widgets import Worker

INVALID_NAME = re.compile(r'[\\/:*?"<>|]')
IMAGE_FILTER = "图片或 PDF (*.jpg *.jpeg *.png *.pdf);;所有文件 (*.*)"
MAX_SIDE = 1600
MAX_PDF_PAGES = 4
RULES_EXAMPLE = "例如：1-20 题每题 2 分；21-25 题为多选题，每题 4 分，漏选得 1 分，错选不得分；26-30 题每题 3 分，答错扣 1 分。"


def load_images(path: str) -> List[bytes]:
    """读取图片或 PDF（最多前几页），缩放到合适大小后返回 JPEG 字节。"""
    images: List[QImage] = []
    if path.lower().endswith(".pdf"):
        import fitz

        with fitz.open(path) as document:
            for index in range(min(len(document), MAX_PDF_PAGES)):
                pixmap = document[index].get_pixmap(matrix=fitz.Matrix(2, 2))
                images.append(QImage.fromData(pixmap.tobytes("png")))
    else:
        images.append(QImage(path))
    result = []
    for image in images:
        if image.isNull():
            raise ValueError(f"无法读取图片：{path}")
        if max(image.width(), image.height()) > MAX_SIDE:
            image = image.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "JPG", 85)
        result.append(bytes(data))
    return result


class AnswerPage(QWidget):
    changed = Signal()

    COLUMNS = ["题号", "正确答案", "答对分值", "答错分值", "漏选得分"]

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._updating = False
        self._rows: Dict[str, AnswerRow] = {}
        self._marking = Marking()
        self._unsupported = ""
        self._worker = None
        self._import_job = EngineJob(self)
        self._import_job.finished.connect(self._on_card_imported)
        self._import_output = None
        self._build_ui()
        ctx.projectChanged.connect(self.reload)
        ctx.settingsChanged.connect(self._update_ai_buttons)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        set_row = QHBoxLayout()
        set_row.addWidget(QLabel("当前答案"))
        self.answer_combo = QComboBox()
        self.answer_combo.setMinimumWidth(200)
        self.answer_combo.activated.connect(self._on_answer_chosen)
        set_row.addWidget(self.answer_combo)
        for text, callback in [
            ("新建", self._on_answer_new),
            ("复制", self._on_answer_copy),
            ("重命名", self._on_answer_rename),
            ("删除", self._on_answer_delete),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            set_row.addWidget(button)
        hint = QLabel("同一个版面可以保存多套答案（如不同场次），阅卷和扫描时再选用。")
        hint.setObjectName("hint")
        set_row.addWidget(hint)
        set_row.addStretch(1)
        layout.addLayout(set_row)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("color:#b54708;background:#fffaeb;padding:8px 12px;border-radius:4px;")
        self.banner.hide()
        layout.addWidget(self.banner)

        score_box = QGroupBox("默认分值")
        score_row = QHBoxLayout(score_box)
        self.correct_edit = QLineEdit("1")
        self.incorrect_edit = QLineEdit("0")
        self.unmarked_edit = QLineEdit("0")
        for title, edit in [("答对", self.correct_edit), ("答错", self.incorrect_edit), ("未答", self.unmarked_edit)]:
            edit.setMaximumWidth(80)
            edit.editingFinished.connect(self._on_marking_edited)
            score_row.addWidget(QLabel(title))
            score_row.addWidget(edit)
        note = QLabel("答错可填负数（如 -0.5），分数可填 3/2。")
        note.setObjectName("hint")
        score_row.addWidget(note)
        score_row.addStretch(1)
        layout.addWidget(score_box)

        rules_box = QGroupBox("评分规则（用自然语言描述，由大模型转换）")
        rules_layout = QVBoxLayout(rules_box)
        self.rules_edit = QPlainTextEdit()
        self.rules_edit.setPlaceholderText(RULES_EXAMPLE)
        self.rules_edit.setMaximumHeight(72)
        rules_layout.addWidget(self.rules_edit)
        rules_row = QHBoxLayout()
        self.btn_rules = QPushButton("生成评分设置")
        self.btn_rules.clicked.connect(self._on_generate_rules)
        rules_row.addWidget(self.btn_rules)
        rules_row.addStretch(1)
        rules_layout.addLayout(rules_row)
        layout.addWidget(rules_box)

        fill_box = QGroupBox("录入答案")
        fill_layout = QVBoxLayout(fill_box)
        import_row = QHBoxLayout()
        self.btn_card = QPushButton("从填涂好的答案卡导入…")
        self.btn_card.clicked.connect(self._on_import_card)
        self.btn_table = QPushButton("从答案表图片 / PDF 导入…")
        self.btn_table.clicked.connect(self._on_import_table)
        import_row.addWidget(self.btn_card)
        import_row.addWidget(self.btn_table)
        import_row.addStretch(1)
        fill_layout.addLayout(import_row)
        fill_row = QHBoxLayout()
        self.fill_edit = QLineEdit()
        self.fill_edit.setPlaceholderText("按题号顺序输入答案，如 ABCDDBCA；多选题用逗号分隔：A,BC,D")
        btn_fill = QPushButton("按顺序填入")
        btn_fill.clicked.connect(self._on_quick_fill)
        btn_clear = QPushButton("清空全部答案")
        btn_clear.clicked.connect(self._on_clear)
        fill_row.addWidget(self.fill_edit, 1)
        fill_row.addWidget(btn_fill)
        fill_row.addWidget(btn_clear)
        fill_layout.addLayout(fill_row)
        layout.addWidget(fill_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        tip = QLabel(
            "答案留空的题目不计分。同一题有多个正确答案时用竖线分隔，如 A|C；要求同时涂两个选项时直接写 AB。"
            "分值留空则用默认；“漏选得分”仅对多选题（答案为两个及以上选项，如 ABD）有效，选对一部分且没选错时按该分值计分。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.rebuild_button = QPushButton("放弃当前答案文件，重新建立")
        self.rebuild_button.clicked.connect(self._rebuild)
        self.rebuild_button.hide()
        layout.addWidget(self.rebuild_button)
        self._update_ai_buttons()

    def _update_ai_buttons(self):
        ready = ai_assist.configured(self.ctx.settings)
        suffix = "" if ready else "（需在设置中配置大模型）"
        self.btn_rules.setEnabled(ready)
        self.btn_table.setEnabled(ready)
        self.btn_rules.setText("生成评分设置" + suffix)
        self.btn_table.setText("从答案表图片 / PDF 导入…" + suffix)

    def show_status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet(
            "color:#b42318;background:#fef3f2;padding:6px 10px;border-radius:4px;"
            if error
            else "color:#175cd3;background:#eff8ff;padding:6px 10px;border-radius:4px;"
        )
        self.status.show()

    # ---------- 数据装载 ----------

    @property
    def project(self):
        return self.ctx.project

    def _question_labels(self) -> List[str]:
        project = self.project
        combined = set()
        for labels in project.template.get("customLabels", {}).values():
            for item in labels:
                try:
                    combined.update(expand_label_item(item))
                except ValueError:
                    combined.add(item)
        return [label for label in project.all_labels() if label not in combined]

    def _ordered_rows(self) -> List[AnswerRow]:
        return [self._rows[label] for label in self._question_labels() if label in self._rows]

    def reload(self):
        self._rows = {}
        self._marking = Marking()
        self._unsupported = ""
        self.status.hide()
        project = self.project
        self.setEnabled(project is not None)
        if project and project.evaluation:
            try:
                rows, marking = read_evaluation(project.evaluation)
                self._rows = {r.label: r for r in rows}
                self._marking = marking
            except (ValueError, KeyError) as error:
                self._unsupported = str(error)
        self._update_banner()
        self._refresh_answer_combo()
        self.refresh()

    def _refresh_answer_combo(self):
        self._updating = True
        self.answer_combo.clear()
        project = self.project
        if project:
            names = project.answer_names() or [project.active_answer]
            for name in names:
                self.answer_combo.addItem(name, name)
            index = self.answer_combo.findData(project.active_answer)
            self.answer_combo.setCurrentIndex(max(index, 0))
        self._updating = False

    def _switch_after_change(self):
        """答案集合变动后重新载入页面，并通知其他页面刷新选择。"""
        self.reload()
        self.changed.emit()
        self.ctx.answersChanged.emit()

    def _on_answer_chosen(self, index):
        if self._updating or not self.project:
            return
        name = self.answer_combo.itemData(index)
        if name == self.project.active_answer:
            return
        self.commit()
        self.project.switch_answer(name)
        self._switch_after_change()

    def _ask_answer_name(self, title, default=""):
        name, ok = QInputDialog.getText(self, title, "答案名称", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if INVALID_NAME.search(name):
            QMessageBox.warning(self, "名称不合法", "名称不能包含 反斜杠、/ : * ? 双引号 < > | 等字符。")
            return None
        return name

    def _on_answer_new(self):
        self._create_answer("新建答案", copy=False)

    def _on_answer_copy(self):
        self._create_answer("复制当前答案", copy=True)

    def _create_answer(self, title, copy):
        if not self.project:
            return
        name = self._ask_answer_name(title, f"{self.project.active_answer} 副本" if copy else "")
        if not name:
            return
        self.commit()
        try:
            self.project.new_answer(name, copy_current=copy)
        except FileExistsError as error:
            QMessageBox.warning(self, title, str(error))
            return
        self._switch_after_change()

    def _on_answer_rename(self):
        if not self.project:
            return
        name = self._ask_answer_name("重命名答案", self.project.active_answer)
        if not name:
            return
        self.commit()
        try:
            self.project.rename_answer(name)
        except FileExistsError as error:
            QMessageBox.warning(self, "重命名答案", str(error))
            return
        self._switch_after_change()

    def _on_answer_delete(self):
        if not self.project:
            return
        answer = QMessageBox.question(
            self, "删除答案", f"确定删除答案“{self.project.active_answer}”吗？此操作不可恢复。"
        )
        if answer != QMessageBox.Yes:
            return
        self.project.delete_answer()
        self._switch_after_change()

    def _update_banner(self):
        unsupported = bool(self._unsupported)
        self.banner.setVisible(unsupported)
        self.rebuild_button.setVisible(unsupported)
        if unsupported:
            self.banner.setText(
                f"当前方案的答案文件包含本页不支持编辑的内容（{self._unsupported}），评分仍按原文件执行。"
                "如需在此编辑，请重新建立。"
            )
        self.table.setEnabled(not unsupported)

    def _rebuild(self):
        answer = QMessageBox.question(self, "重新建立", "将丢弃当前答案文件中的全部内容，是否继续？")
        if answer != QMessageBox.Yes:
            return
        self._unsupported = ""
        self._rows = {}
        self.project.evaluation = None
        self._update_banner()
        self.refresh()
        self.changed.emit()

    def refresh(self):
        """按模板最新题目重建表格，保留已录入的答案。"""
        if not self.project:
            self.table.setRowCount(0)
            return
        self._updating = True
        labels = self._question_labels()
        self.table.setRowCount(len(labels))
        for index, label in enumerate(labels):
            row = self._rows.setdefault(label, AnswerRow(label))
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(index, 0, label_item)
            self.table.setItem(index, 1, QTableWidgetItem(row.answer))
            self.table.setItem(index, 2, QTableWidgetItem(row.score))
            self.table.setItem(index, 3, QTableWidgetItem(row.incorrect))
            self.table.setItem(index, 4, QTableWidgetItem(row.partial))
        self.correct_edit.setText(self._marking.correct)
        self.incorrect_edit.setText(self._marking.incorrect)
        self.unmarked_edit.setText(self._marking.unmarked)
        self._updating = False

    # ---------- 手动编辑 ----------

    def _on_item_changed(self, item):
        if self._updating:
            return
        label = self.table.item(item.row(), 0).text()
        row = self._rows[label]
        text = item.text().strip()
        if item.column() == 1:
            row.answer = text.upper()
            self._updating = True
            item.setText(row.answer)
            self._updating = False
        elif item.column() == 2:
            row.score = text
        elif item.column() == 3:
            row.incorrect = text
        elif item.column() == 4:
            row.partial = text
        self.commit()

    def _on_marking_edited(self):
        if self._updating:
            return
        self._marking = Marking(
            self.correct_edit.text().strip() or "0",
            self.incorrect_edit.text().strip() or "0",
            self.unmarked_edit.text().strip() or "0",
        )
        self.commit()

    def _on_quick_fill(self):
        text = self.fill_edit.text().strip().upper()
        if not text:
            return
        parts = [p.strip() for p in text.split(",")] if "," in text else list(text.replace(" ", ""))
        labels = self._question_labels()
        if len(parts) > len(labels):
            QMessageBox.warning(self, "答案过多", f"录入了 {len(parts)} 个答案，但模板只有 {len(labels)} 道题。")
            return
        for label, answer in zip(labels, parts):
            self._rows[label].answer = answer
        self.refresh()
        self.commit()

    def _on_clear(self):
        for row in self._rows.values():
            row.answer = ""
        self.refresh()
        self.commit()

    # ---------- 从答案卡导入 ----------

    def _on_import_card(self):
        project = self.project
        if not project or self._import_job.running():
            return
        if not self._question_labels():
            QMessageBox.information(self, "导入", "当前方案还没有字段块，请先完成“版面模板”。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择填涂好的答案卡", "", IMAGE_FILTER)
        if not path:
            return
        project.save()
        folder = temp_path("answer_import")
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        config_path = folder / "config.json"
        write_config(config_path, project.engine_config(self.ctx.settings))
        files_path = folder / "files.txt"
        files_path.write_text(path, "utf-8")
        self._import_output = folder / "out"
        self.btn_card.setEnabled(False)
        self.show_status("正在识别答案卡…")
        self._import_job.start(
            "run",
            project=project.dir,
            config=config_path,
            files=files_path,
            output=self._import_output,
            noeval=1,
        )

    def _on_card_imported(self, result):
        self.btn_card.setEnabled(True)
        if not result.get("ok"):
            self.show_status(f"识别答案卡失败：{result.get('error', '未知错误')}", error=True)
            return
        columns, records = read_run_results(self._import_output)
        if not records:
            self.show_status("没有从答案卡中读出任何结果，请确认答案卡与当前版面一致。", error=True)
            return
        record = records[0]
        filled = blank = 0
        for column, answer in zip(columns, record["answers"]):
            row = self._rows.get(column)
            if row is None:
                continue
            if answer:
                row.answer = answer
                filled += 1
            else:
                blank += 1
        self.refresh()
        self.commit()
        message = f"已从答案卡导入 {filled} 题的答案"
        if blank:
            message += f"，{blank} 题没有填涂，保持为空"
        if record["status"] == "multi":
            message += "。答案卡上存在多涂的题，对应答案显示为多个选项，请核对"
        self.show_status(message + "。")

    # ---------- 大模型：答案表导入与评分规则 ----------

    def _run_worker(self, function, on_done, busy_text):
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_table.setEnabled(False)
        self.btn_rules.setEnabled(False)
        self.show_status(busy_text)
        self._worker = Worker(function)
        self._worker.done.connect(on_done)
        self._worker.start()

    def _on_import_table(self):
        if not self.project or not ai_assist.configured(self.ctx.settings):
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择答案表图片或 PDF", "", IMAGE_FILTER)
        if not path:
            return
        try:
            images = load_images(path)
        except Exception as error:
            self.show_status(f"无法读取文件：{error}", error=True)
            return
        settings = self.ctx.settings
        self._run_worker(
            lambda: ai_assist.extract_answers(settings, images),
            self._on_table_extracted,
            "大模型正在读取答案表…",
        )

    def _on_table_extracted(self, answers, error):
        self._update_ai_buttons()
        if error or answers is None:
            self.show_status(f"大模型读取失败：{error}", error=True)
            return
        filled, unmatched = fill_answers(self._ordered_rows(), answers)
        self.refresh()
        self.commit()
        message = f"已从答案表导入 {filled} 题的答案，请核对后使用"
        if unmatched:
            shown = "、".join(str(n) for n in unmatched[:10])
            message += f"；答案表中的第 {shown} 题在当前模板里没有对应字段"
        self.show_status(message + "。")

    def _on_generate_rules(self):
        text = self.rules_edit.toPlainText().strip()
        if not self.project or not ai_assist.configured(self.ctx.settings):
            return
        if not text:
            self.show_status("请先用文字描述评分规则。", error=True)
            return
        count = len(self._question_labels())
        if not count:
            self.show_status("当前方案还没有题目，请先完成“版面模板”。", error=True)
            return
        settings = self.ctx.settings
        self._run_worker(
            lambda: ai_assist.parse_scoring_rules(settings, text, count),
            self._on_rules_parsed,
            "大模型正在解析评分规则…",
        )

    def _on_rules_parsed(self, reply, error):
        self._update_ai_buttons()
        if error or reply is None:
            self.show_status(f"大模型解析失败：{error}", error=True)
            return
        try:
            marking, affected = apply_rules(self._ordered_rows(), self._marking, reply)
        except (TypeError, ValueError) as error:
            self.show_status(f"评分规则格式不正确：{error}", error=True)
            return
        self._marking = marking
        self.refresh()
        message = self.validate()
        if message:
            self.show_status(f"生成的设置有误：{message}", error=True)
            return
        self.commit()
        self.show_status(
            f"已设置：默认答对 {marking.correct}、答错 {marking.incorrect}、未答 {marking.unmarked}；"
            f"另有 {affected} 题使用了单独设置。请在下方表格核对。"
        )

    # ---------- 校验与提交 ----------

    def validate(self) -> str:
        """返回分值格式错误说明；格式正确时返回空字符串。"""
        for title, value in [
            ("答对", self._marking.correct),
            ("答错", self._marking.incorrect),
            ("未答", self._marking.unmarked),
        ]:
            try:
                parse_score(value)
            except ValueError:
                return f"默认分值“{title}”格式不正确：{value}"
        for row in self._rows.values():
            for title, value in (("答对分值", row.score), ("答错分值", row.incorrect), ("漏选得分", row.partial)):
                if value.strip():
                    try:
                        parse_score(value)
                    except ValueError:
                        return f"{row.label} 的{title}格式不正确：{value}"
            if row.partial.strip() and row.answer.strip() and not has_partial(row):
                return f"{row.label} 设置了漏选得分，但答案不是多选题（需要两个及以上选项，且不含“|”）"
        return ""

    def commit(self):
        """将答案表写回方案对象；格式错误时不写入。"""
        if self._updating or not self.project or self._unsupported:
            return
        if self.validate():
            return
        self.project.evaluation = build_evaluation(self._ordered_rows(), self._marking)
        self.changed.emit()
