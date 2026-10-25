"""方案模型：模板字段块与外框的互转、标签解析、答案文件读写、方案目录管理。"""
import json
import re
import shutil
from fractions import Fraction
from itertools import combinations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FIELD_TYPE_PRESETS = {
    "QTYPE_MCQ4": ("选择题 4 项（A-D）", ["A", "B", "C", "D"], "horizontal"),
    "QTYPE_MCQ5": ("选择题 5 项（A-E）", ["A", "B", "C", "D", "E"], "horizontal"),
    "QTYPE_MCQ4_RTL": ("选择题 4 项（D-A）", ["D", "C", "B", "A"], "horizontal"),
    "QTYPE_MCQ5_RTL": ("选择题 5 项（E-A）", ["E", "D", "C", "B", "A"], "horizontal"),
    "QTYPE_INT": ("数字 0-9（纵向）", [str(i) for i in range(10)], "vertical"),
    "QTYPE_INT_FROM_1": (
        "数字 1-9、0（纵向）",
        [str(i) for i in list(range(1, 10)) + [0]],
        "vertical",
    ),
}
CUSTOM_TYPE = "__CUSTOM__"

_KNOWN_BLOCK_KEYS = {
    "origin",
    "bubblesGap",
    "labelsGap",
    "fieldLabels",
    "fieldType",
    "bubbleValues",
    "direction",
    "bubbleDimensions",
}

PREPROCESSORS = {
    "CropPage": "裁剪页面",
    "CropOnMarkers": "标记点定位裁剪",
    "Levels": "色阶调整",
    "GaussianBlur": "高斯模糊",
    "MedianBlur": "中值模糊",
    "FeatureBasedAlignment": "特征点对齐",
}

_RANGE_RE = re.compile(r"^([^\.\d]+)(\d+)\.{2,3}(\d+)$")


def split_label_text(text) -> List[str]:
    return [p.strip() for p in re.split(r"[,，]", text or "") if p.strip()]


def expand_label_item(item) -> List[str]:
    """展开单个标签项，如 q1..5 → q1~q5；格式错误时抛出 ValueError。"""
    if "." not in item:
        return [item]
    match = _RANGE_RE.match(item)
    if not match:
        raise ValueError(f"字段标签格式不正确：{item}")
    prefix, start, end = match.group(1), int(match.group(2)), int(match.group(3))
    if start >= end:
        raise ValueError(f"范围起始必须小于结束：{item}")
    return [f"{prefix}{i}" for i in range(start, end + 1)]


def expand_labels(items) -> List[str]:
    labels = []
    for item in items:
        labels.extend(expand_label_item(item))
    if len(set(labels)) != len(labels):
        raise ValueError("字段标签存在重复")
    return labels


def parse_labels(text) -> List[str]:
    """将“q1..20, roll1..9, name”展开为字段标签列表；格式错误时抛出 ValueError。"""
    return expand_labels(split_label_text(text))


def make_label_text(prefix, start, count) -> str:
    if count <= 1:
        return f"{prefix}{start}"
    return f"{prefix}{start}..{start + count - 1}"


@dataclass
class Block:
    """字段块：外框 (x, y, w, h) 为首个气泡左上角到末个气泡右下角的范围。"""

    name: str
    field_type: str = "QTYPE_MCQ4"
    values: List[str] = field(default_factory=lambda: ["A", "B", "C", "D"])
    direction: str = "horizontal"
    label_items: List[str] = field(default_factory=lambda: ["q1..10"])
    x: float = 0
    y: float = 0
    w: float = 100
    h: float = 100
    bubble_dims: Optional[Tuple[float, float]] = None
    gap_hint: Tuple[float, float] = (0, 0)
    extra: dict = field(default_factory=dict)

    @property
    def labels(self) -> str:
        return ",".join(self.label_items)

    @labels.setter
    def labels(self, text: str):
        self.label_items = split_label_text(text)

    def set_type(self, field_type):
        self.field_type = field_type
        if field_type in FIELD_TYPE_PRESETS:
            _, values, direction = FIELD_TYPE_PRESETS[field_type]
            self.values = list(values)
            self.direction = direction

    def label_count(self) -> int:
        try:
            return len(expand_labels(self.label_items))
        except ValueError:
            return 0

    def gaps(self, default_dims) -> Tuple[float, float]:
        """返回 (气泡间距, 字段间距)，由外框和数量反推。"""
        bw, bh = self.bubble_dims or default_dims
        nv, nl = len(self.values), max(self.label_count(), 1)
        hint_bubbles, hint_labels = self.gap_hint
        if self.direction == "horizontal":
            bubbles = (self.w - bw) / (nv - 1) if nv > 1 else hint_bubbles
            labels = (self.h - bh) / (nl - 1) if nl > 1 else hint_labels
        else:
            bubbles = (self.h - bh) / (nv - 1) if nv > 1 else hint_bubbles
            labels = (self.w - bw) / (nl - 1) if nl > 1 else hint_labels
        return max(bubbles, 0), max(labels, 0)

    def bubble_origins(self, default_dims):
        """返回每个字段的气泡左上角坐标列表，外层为字段、内层为选项。"""
        bubbles_gap, labels_gap = self.gaps(default_dims)
        rows = []
        for i in range(max(self.label_count(), 0)):
            row = []
            for j in range(len(self.values)):
                if self.direction == "horizontal":
                    row.append((self.x + j * bubbles_gap, self.y + i * labels_gap))
                else:
                    row.append((self.x + i * labels_gap, self.y + j * bubbles_gap))
            rows.append(row)
        return rows

    def to_json(self, default_dims) -> dict:
        bubbles_gap, labels_gap = self.gaps(default_dims)
        data = {
            **self.extra,
            "origin": [int(round(self.x)), int(round(self.y))],
            "bubblesGap": round(bubbles_gap, 2),
            "labelsGap": round(labels_gap, 2),
            "fieldLabels": list(self.label_items),
        }
        if self.field_type in FIELD_TYPE_PRESETS:
            data["fieldType"] = self.field_type
        else:
            data["bubbleValues"] = list(self.values)
            data["direction"] = self.direction
        if self.bubble_dims:
            data["bubbleDimensions"] = [self.bubble_dims[0], self.bubble_dims[1]]
        return data

    @staticmethod
    def from_json(name, data, default_dims) -> "Block":
        block = Block(name=name)
        field_type = data.get("fieldType", CUSTOM_TYPE)
        if field_type in FIELD_TYPE_PRESETS:
            block.set_type(field_type)
        else:
            block.field_type = CUSTOM_TYPE
            block.values = list(data.get("bubbleValues", ["A", "B", "C", "D"]))
            block.direction = data.get("direction", "vertical")
        if "direction" in data:
            block.direction = data["direction"]
        if "bubbleValues" in data:
            block.values = list(data["bubbleValues"])
        block.label_items = list(data.get("fieldLabels", []))
        block.extra = {k: v for k, v in data.items() if k not in _KNOWN_BLOCK_KEYS}
        dims = data.get("bubbleDimensions")
        block.bubble_dims = (dims[0], dims[1]) if dims else None
        bw, bh = block.bubble_dims or default_dims
        origin = data.get("origin", [0, 0])
        block.x, block.y = origin[0], origin[1]
        bubbles_gap = data.get("bubblesGap", 0)
        labels_gap = data.get("labelsGap", 0)
        block.gap_hint = (bubbles_gap, labels_gap)
        nv, nl = len(block.values), max(block.label_count(), 1)
        if block.direction == "horizontal":
            block.w = bubbles_gap * (nv - 1) + bw
            block.h = labels_gap * (nl - 1) + bh
        else:
            block.w = labels_gap * (nl - 1) + bw
            block.h = bubbles_gap * (nv - 1) + bh
        return block


def new_template(page_w, page_h) -> dict:
    return {
        "pageDimensions": [int(page_w), int(page_h)],
        "bubbleDimensions": [20, 20],
        "preProcessors": [],
        "customLabels": {},
        "fieldBlocks": {},
    }


def default_preprocessor_options(name) -> dict:
    return {
        "CropPage": {"morphKernel": [10, 10]},
        "CropOnMarkers": {
            "relativePath": "omr_marker.jpg",
            "sheetToMarkerWidthRatio": 17,
        },
        "Levels": {"low": 0.0, "high": 1.0, "gamma": 1.0},
        "GaussianBlur": {"kSize": [3, 3], "sigmaX": 0},
        "MedianBlur": {"kSize": 5},
        "FeatureBasedAlignment": {"reference": "reference.jpg", "maxFeatures": 500},
    }[name]


def dump_json(path, data):
    """写出引擎可读取的 JSON（转义非 ASCII 字符，避免依赖系统默认编码）。"""
    Path(path).write_text(json.dumps(data, ensure_ascii=True, indent=2), "utf-8")


_SCORE_RE = re.compile(r"^-?\d+(/\d+)?$")


def parse_score(text):
    """将分值文本转为评分文件接受的数值或分数字符串；无法识别时抛出 ValueError。"""
    text = str(text).strip()
    if _SCORE_RE.match(text):
        return int(text) if "/" not in text else text
    return float(text)


@dataclass
class AnswerRow:
    """一道题的答案与评分：score 为答对分值，incorrect 为答错分值，partial 为多选题漏选得分，空表示用默认。"""

    label: str
    answer: str = ""
    score: str = ""
    incorrect: str = ""
    partial: str = ""


@dataclass
class Marking:
    correct: str = "1"
    incorrect: str = "0"
    unmarked: str = "0"


def weighted_score(text):
    """加权答案只接受整数或分数字符串，把 0.5 之类的小数转为 1/2。"""
    value = Fraction(str(text).strip()).limit_denominator(1000)
    return int(value) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def has_partial(row: AnswerRow) -> bool:
    return bool(row.partial.strip()) and "|" not in row.answer and len(row.answer.strip()) >= 2


def weighted_answer(answer: str, full: str, partial: str) -> list:
    """多选题漏选给分：正确答案的全部真子集得 partial，完整答案得 full，其余作答按答错计分。"""
    letters = list(answer.strip())
    pairs = []
    for size in range(len(letters), 0, -1):
        for combo in combinations(letters, size):
            score = full if size == len(letters) else partial
            pairs.append(["".join(combo), weighted_score(score)])
    return pairs


def build_evaluation(rows: List[AnswerRow], marking: Marking) -> Optional[dict]:
    """由答案表生成 evaluation.json；没有任何答案时返回 None。"""
    graded = [r for r in rows if r.answer.strip()]
    if not graded:
        return None
    default_correct = marking.correct.strip()
    default_incorrect = marking.incorrect.strip()
    answers = []
    for row in graded:
        if has_partial(row):
            answers.append(
                weighted_answer(row.answer, row.score.strip() or default_correct, row.partial)
            )
            continue
        options = [a.strip() for a in row.answer.split("|") if a.strip()]
        answers.append(options[0] if len(options) == 1 else options)
    schemes = {
        "DEFAULT": {
            "correct": parse_score(marking.correct),
            "incorrect": parse_score(marking.incorrect),
            "unmarked": parse_score(marking.unmarked),
        }
    }
    groups: Dict[tuple, List[str]] = {}
    for row in graded:
        correct = row.score.strip() or default_correct
        incorrect = row.incorrect.strip() or default_incorrect
        if correct != default_correct or incorrect != default_incorrect:
            groups.setdefault((correct, incorrect), []).append(row.label)
    for index, ((correct, incorrect), labels) in enumerate(groups.items(), start=1):
        schemes[f"SCORE_{index}"] = {
            "questions": labels,
            "marking": {
                "correct": parse_score(correct),
                "incorrect": parse_score(incorrect),
                "unmarked": parse_score(marking.unmarked),
            },
        }
    return {
        "source_type": "custom",
        "options": {
            "questions_in_order": [r.label for r in graded],
            "answers_in_order": answers,
            "should_explain_scoring": False,
        },
        "marking_schemes": schemes,
    }


def _score_text(value) -> str:
    return str(value)


def _read_weighted(answer: list):
    """解析 weighted_answer 生成的加权答案，返回 (完整答案, 完整分, 漏选分)；结构不符时抛出 ValueError。"""
    pairs = [(item[0], _score_text(item[1])) for item in answer]
    full_answer, full_score = max(pairs, key=lambda p: len(p[0]))
    expected = {"".join(c) for size in range(1, len(full_answer) + 1) for c in combinations(full_answer, size)}
    if {p[0] for p in pairs} != expected:
        raise ValueError("答案包含加权设置，不能在此编辑")
    partial_scores = {s for a, s in pairs if a != full_answer}
    if len(partial_scores) > 1:
        raise ValueError("答案包含加权设置，不能在此编辑")
    return full_answer, full_score, (partial_scores.pop() if partial_scores else "")


def read_evaluation(evaluation: dict):
    """解析 evaluation.json 为 (答案表, 默认分值)；包含本程序不支持的内容时抛出 ValueError。"""
    if evaluation.get("source_type") != "custom":
        raise ValueError("答案来自 CSV 文件，不能在此编辑")
    options = evaluation["options"]
    questions: List[str] = []
    for item in options["questions_in_order"]:
        questions.extend(expand_label_item(item))
    answers = options["answers_in_order"]
    if len(questions) != len(answers):
        raise ValueError("题目数量与答案数量不一致")
    schemes = evaluation["marking_schemes"]
    default = schemes["DEFAULT"]
    marking = Marking(
        str(default["correct"]), str(default["incorrect"]), str(default["unmarked"])
    )
    scores = {}
    incorrects = {}
    for key, scheme in schemes.items():
        if key == "DEFAULT":
            continue
        qs = scheme["questions"]
        for q in [qs] if isinstance(qs, str) else qs:
            for label in expand_label_item(q):
                if str(scheme["marking"]["correct"]) != marking.correct:
                    scores[label] = str(scheme["marking"]["correct"])
                if str(scheme["marking"]["incorrect"]) != marking.incorrect:
                    incorrects[label] = str(scheme["marking"]["incorrect"])
    rows = []
    for label, answer in zip(questions, answers):
        score = scores.get(label, "")
        partial = ""
        if isinstance(answer, list) and answer and all(isinstance(a, list) for a in answer):
            text, full, partial = _read_weighted(answer)
            if full != marking.correct:
                score = full
        elif isinstance(answer, list):
            text = "|".join(answer)
        else:
            text = answer
        rows.append(AnswerRow(label, text, score, incorrects.get(label, ""), partial))
    return rows, marking


class Project:
    TEMPLATE = "template.json"
    EVALUATION = "evaluation.json"
    META = "project.json"

    def __init__(self, directory):
        self.dir = Path(directory)
        self.template: dict = new_template(666, 820)
        self.evaluation: Optional[dict] = None
        self.meta: dict = {}

    @property
    def name(self):
        return self.dir.name

    @property
    def reference_path(self) -> Optional[Path]:
        name = self.meta.get("reference")
        return self.dir / name if name else None

    @property
    def processing(self) -> Optional[Tuple[int, int]]:
        value = self.meta.get("processing")
        return (value[0], value[1]) if value else None

    ANSWERS_DIR = "answers"
    DEFAULT_ANSWER = "默认"

    @property
    def answers_dir(self) -> Path:
        return self.dir / self.ANSWERS_DIR

    def answer_names(self) -> List[str]:
        """方案内全部答案的名称（按名称排序）。"""
        if not self.answers_dir.exists():
            return []
        return sorted(p.stem for p in self.answers_dir.glob("*.json"))

    @property
    def active_answer(self) -> str:
        return self.meta.get("answer") or self.DEFAULT_ANSWER

    def answer_path(self, name: str) -> Path:
        return self.answers_dir / f"{name}.json"

    def _read_answer(self, name: str) -> Optional[dict]:
        path = self.answer_path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) and data else None

    @property
    def text_fields(self) -> List[dict]:
        """文字区域（姓名、班级等）：每项含 name、x、y、w、h，坐标与模板一致。"""
        return self.meta.setdefault("text_fields", [])

    def write_text_fields(self, path):
        dump_json(path, self.text_fields)

    def load(self):
        template_path = self.dir / self.TEMPLATE
        if template_path.exists():
            self.template = json.loads(template_path.read_text("utf-8"))
        meta_path = self.dir / self.META
        if meta_path.exists():
            self.meta = json.loads(meta_path.read_text("utf-8"))
        legacy_path = self.dir / self.EVALUATION
        names = self.answer_names()
        if names:
            if self.meta.get("answer") not in names:
                self.meta["answer"] = names[0]
            self.evaluation = self._read_answer(self.active_answer)
        elif legacy_path.exists():
            self.evaluation = json.loads(legacy_path.read_text("utf-8"))
            self.meta["answer"] = self.DEFAULT_ANSWER
        else:
            self.evaluation = None
        return self

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        dump_json(self.dir / self.TEMPLATE, self.template)
        self.meta["answer"] = self.active_answer
        self.answers_dir.mkdir(exist_ok=True)
        dump_json(self.answer_path(self.active_answer), self.evaluation or {})
        legacy_path = self.dir / self.EVALUATION
        if self.evaluation:
            dump_json(legacy_path, self.evaluation)
        elif legacy_path.exists():
            legacy_path.unlink()
        (self.dir / self.META).write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2), "utf-8"
        )

    def switch_answer(self, name: str):
        """保存当前答案后切换到另一套答案。"""
        self.save()
        self.meta["answer"] = name
        self.evaluation = self._read_answer(name)
        self.save()

    def new_answer(self, name: str, copy_current: bool = False):
        """新建一套答案并切换过去；copy_current 为真时以当前答案为底稿。"""
        if self.answer_path(name).exists():
            raise FileExistsError(f"答案已存在：{name}")
        self.save()
        content = json.loads(json.dumps(self.evaluation)) if (copy_current and self.evaluation) else None
        self.meta["answer"] = name
        self.evaluation = content
        self.save()

    def rename_answer(self, name: str):
        old = self.active_answer
        if name == old:
            return
        if self.answer_path(name).exists():
            raise FileExistsError(f"答案已存在：{name}")
        self.save()
        self.answer_path(old).rename(self.answer_path(name))
        self.meta["answer"] = name

    def delete_answer(self):
        """删除当前答案；删完后切到剩下的第一套，没有则新建空的默认答案。"""
        self.answer_path(self.active_answer).unlink(missing_ok=True)
        names = self.answer_names()
        self.meta["answer"] = names[0] if names else self.DEFAULT_ANSWER
        self.evaluation = self._read_answer(self.meta["answer"])
        self.save()

    def set_reference(self, source):
        """将空白答题卡复制进方案目录并记录。"""
        source = Path(source)
        target = self.dir / f"reference{source.suffix.lower()}"
        self.dir.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        self.meta["reference"] = target.name

    def set_aux_image(self, source, stem):
        """将辅助图片（标记点、对齐参考图）复制进方案目录，返回方案内文件名。"""
        source = Path(source)
        target = self.dir / f"{stem}{source.suffix.lower()}"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return target.name

    def set_marker(self, source):
        return self.set_aux_image(source, "omr_marker")

    @property
    def default_dims(self):
        dims = self.template.get("bubbleDimensions", [20, 20])
        return dims[0], dims[1]

    def blocks(self) -> List[Block]:
        return [
            Block.from_json(name, data, self.default_dims)
            for name, data in self.template.get("fieldBlocks", {}).items()
        ]

    def set_blocks(self, blocks: List[Block]):
        self.template["fieldBlocks"] = {
            b.name: b.to_json(self.default_dims) for b in blocks
        }

    def all_labels(self) -> List[str]:
        """按字段块顺序返回全部字段标签（不含合并列）。"""
        labels = []
        for data in self.template.get("fieldBlocks", {}).values():
            for item in data.get("fieldLabels", []):
                try:
                    labels.extend(expand_label_item(item))
                except ValueError:
                    labels.append(item)
        return labels

    def engine_config(self, settings) -> dict:
        config = settings.engine_config()
        if self.processing:
            config["dimensions"]["processing_width"] = self.processing[0]
            config["dimensions"]["processing_height"] = self.processing[1]
        return config


def list_projects(root) -> List[Path]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and (d / Project.TEMPLATE).exists()),
        key=lambda d: d.name,
    )


def create_project(root, name, page_dims) -> Project:
    directory = Path(root) / name
    if directory.exists():
        raise FileExistsError(f"方案已存在：{name}")
    project = Project(directory)
    project.template = new_template(*page_dims)
    project.save()
    return project


def duplicate_project(source: Project, new_name) -> Project:
    target = source.dir.parent / new_name
    if target.exists():
        raise FileExistsError(f"方案已存在：{new_name}")
    shutil.copytree(source.dir, target)
    return Project(target).load()


def delete_project(project: Project):
    shutil.rmtree(project.dir)
