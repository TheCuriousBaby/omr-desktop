"""文字区域识别结果的读写：每次阅卷目录下的 TextFields.csv，以及该次使用的文字区域名称。"""
import csv
import json
from pathlib import Path
from typing import Dict, List

TEXT_CSV = "TextFields.csv"
FIELDS_JSON = Path("_run") / "textfields.json"


def field_names(run_dir: Path) -> List[str]:
    """本次阅卷定义的文字区域名称；没有定义时返回空列表。"""
    path = Path(run_dir) / FIELDS_JSON
    if not path.exists():
        return []
    try:
        return [item["name"] for item in json.loads(path.read_text("utf-8"))]
    except (ValueError, KeyError, TypeError):
        return []


def load_texts(run_dir: Path) -> Dict[str, Dict[str, str]]:
    """返回 {文件名: {字段名: 文字}}。"""
    path = Path(run_dir) / TEXT_CSV
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return {row["file_id"]: {k: v for k, v in row.items() if k != "file_id"} for row in reader}


def save_texts(run_dir: Path, names: List[str], texts: Dict[str, Dict[str, str]]):
    path = Path(run_dir) / TEXT_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file_id"] + names)
        for file, values in sorted(texts.items()):
            writer.writerow([file] + [values.get(name, "") for name in names])
