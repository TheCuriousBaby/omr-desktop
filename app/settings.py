"""全局设置：参数规格、持久化，以及由设置生成引擎 config.json。"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class Field:
    key: str
    label: str
    kind: str  # int / float / bool / enum / dir / dpi / pages
    default: Any
    help: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: List = field(default_factory=list)  # [(值, 显示名)]


def _home_override():
    """环境变量 OMR_DESKTOP_HOME 指定时，设置文件与默认数据目录都放在该目录下。"""
    value = os.environ.get("OMR_DESKTOP_HOME")
    return Path(value) if value else None


def _documents_dir():
    return _home_override() or Path.home() / "Documents" / "OMR阅卷助手"


GROUPS = [
    (
        "识别参数",
        [
            Field(
                "threshold_params.PAGE_TYPE_FOR_THRESHOLD",
                "纸张类型",
                "enum",
                "white",
                "白底深色笔迹选“白底”；深色底纸选“黑底”。",
                choices=[("white", "白底"), ("black", "黑底")],
            ),
            Field(
                "threshold_params.GAMMA_LOW",
                "阴影压制系数",
                "float",
                0.7,
                "取值 0~1，越小对阴影与折痕越不敏感，但浅色涂痕更易被忽略。",
                0,
                1,
            ),
            Field(
                "threshold_params.MIN_GAP",
                "最小灰度间隔",
                "int",
                30,
                "区分“已涂”与“未涂”所需的最小灰度差，取值 10~100。",
                10,
                100,
            ),
            Field(
                "threshold_params.MIN_JUMP",
                "最小灰度跳变",
                "int",
                25,
                "寻找阈值时要求的最小灰度跳变，取值 10~100。涂得浅时适当调小。",
                10,
                100,
            ),
            Field(
                "threshold_params.JUMP_DELTA",
                "跳变容差",
                "int",
                30,
                "阈值候选之间允许的灰度差，取值 10~100。",
                10,
                100,
            ),
            Field(
                "threshold_params.CONFIDENT_SURPLUS",
                "置信余量",
                "int",
                5,
                "判定整页阈值可信时的灰度余量，取值 0~20。",
                0,
                20,
            ),
        ],
    ),
    (
        "图像尺寸",
        [
            Field(
                "dimensions.processing_width",
                "处理宽度（像素）",
                "int",
                666,
                "识别前先把图片缩放到该宽度；模板坐标基于预处理后的页面。",
                200,
                4000,
            ),
            Field(
                "dimensions.processing_height",
                "处理高度（像素）",
                "int",
                820,
                "识别前先把图片缩放到该高度。",
                200,
                4000,
            ),
        ],
    ),
    (
        "自动对齐",
        [
            Field(
                "alignment_params.auto_align",
                "启用自动对齐",
                "bool",
                False,
                "实验功能。扫描件存在轻微错位时开启。",
            ),
            Field(
                "alignment_params.match_col",
                "匹配列数",
                "int",
                5,
                "对齐时参与匹配的气泡列数，取值 0~10。",
                0,
                10,
            ),
            Field(
                "alignment_params.max_steps",
                "最大步数",
                "int",
                20,
                "每个字段块最多移动的步数，取值 1~100。",
                1,
                100,
            ),
            Field(
                "alignment_params.stride",
                "步长（像素）",
                "int",
                1,
                "每步移动的像素数，取值 1~10。",
                1,
                10,
            ),
            Field(
                "alignment_params.thickness",
                "线宽（像素）",
                "int",
                3,
                "对齐时检测的线条宽度，取值 1~10。",
                1,
                10,
            ),
        ],
    ),
    (
        "PDF 输入",
        [
            Field(
                "pdf_params.pdf_dpi",
                "渲染分辨率（DPI）",
                "dpi",
                0,
                "取 0 表示自动检测，否则取值 72~600。",
                0,
                600,
            ),
            Field(
                "pdf_params.pdf_page",
                "读取页码",
                "pages",
                "",
                "留空表示全部页；可填 1、1,3 或 2-5。",
            ),
        ],
    ),
    (
        "输出",
        [
            Field(
                "outputs.save_detections",
                "保存标注图",
                "bool",
                True,
                "为每张答题卡保存标出识别结果的图片，便于核对。",
            ),
            Field(
                "outputs.filter_out_multimarked_files",
                "多选卷单独归类",
                "bool",
                False,
                "开启后，存在多涂的答题卡不进入结果表，改记入“多涂”清单。",
            ),
            Field(
                "outputs.save_image_level",
                "中间过程图级别",
                "int",
                0,
                "调试用，取值 0~6。级别越高，保存的中间步骤图越多。",
                0,
                6,
            ),
        ],
    ),
    (
        "大模型辅助",
        [
            Field(
                "ai.base_url",
                "接口地址",
                "text",
                "",
                "OpenAI 兼容接口地址，如 https://api.openai.com/v1。用于“自动识别版面”时让大模型判断题型与顺序。",
            ),
            Field("ai.api_key", "密钥", "secret", "", "仅保存在本机设置文件中。"),
            Field("ai.model", "模型名称", "text", "", "需要支持图片输入的模型。"),
            Field(
                "ai.thinking",
                "思考模式",
                "enum",
                "off",
                "仅百炼（dashscope）的 Qwen3 等混合思考模型生效。关闭后响应明显更快；“自动”表示不传该参数，由模型默认决定。",
                choices=[("off", "关闭"), ("on", "开启"), ("auto", "自动（不传参数）")],
            ),
            Field(
                "ai.extra_body",
                "额外请求参数",
                "text",
                "",
                "可选，JSON 对象，会合并进请求体。用于其他厂商的同类开关，如 {\"reasoning_effort\": \"low\"}。",
            ),
            Field("ai.timeout", "超时（秒）", "int", 120, "单次请求的最长等待时间，取值 10~300。视觉模型较慢时可适当调大。", 10, 300),
        ],
    ),
    (
        "常规",
        [
            Field(
                "app.output_root",
                "输出目录",
                "dir",
                str(_documents_dir() / "输出"),
                "每次阅卷会在该目录下按方案名和时间建立子目录。",
            ),
            Field(
                "app.project_root",
                "方案目录",
                "dir",
                str(_documents_dir() / "方案"),
                "模板、答案等方案文件保存位置。",
            ),
            Field(
                "app.open_after_run",
                "完成后打开输出目录",
                "bool",
                False,
                "",
            ),
        ],
    ),
]

ALL_FIELDS = {f.key: f for _, fields in GROUPS for f in fields}


def settings_file():
    override = _home_override()
    if override:
        return override / "settings.json"
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "OMR阅卷助手" / "settings.json"


class AppSettings:
    def __init__(self, path=None):
        self.path = Path(path) if path else settings_file()
        self.values = {k: f.default for k, f in ALL_FIELDS.items()}
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        for key, value in data.items():
            if key in ALL_FIELDS:
                self.values[key] = value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.values, ensure_ascii=False, indent=2), "utf-8"
        )

    def get(self, key):
        return self.values.get(key, ALL_FIELDS[key].default)

    def set(self, key, value):
        self.values[key] = value

    def reset(self, keys=None):
        for key in keys or ALL_FIELDS:
            self.values[key] = ALL_FIELDS[key].default

    def engine_config(self):
        """按当前设置生成 OMRChecker 的 config.json 内容（show_image_level 固定为 0）。"""
        config = {}
        for key, f in ALL_FIELDS.items():
            if key.startswith(("app.", "ai.")):
                continue
            section, name = key.split(".", 1)
            config.setdefault(section, {})[name] = _engine_value(f, self.get(key))
        config["outputs"]["show_image_level"] = 0
        return config


def parse_pages(text):
    """将“1,3,5-8”之类的页码文本转为 pdf_page 配置值；留空返回 None（全部页）。"""
    parts = [p.strip() for p in str(text).replace("，", ",").split(",") if p.strip()]
    if not parts:
        return None
    items = [int(p) if p.isdigit() else p for p in parts]
    return items[0] if len(items) == 1 else items


def _engine_value(f, value):
    if f.kind == "dpi":
        return "auto" if not value else int(value)
    if f.kind == "pages":
        return parse_pages(value)
    if f.kind == "int":
        return int(value)
    if f.kind == "float":
        return float(value)
    if f.kind == "bool":
        return bool(value)
    return value
