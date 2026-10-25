"""将自动识别（可经 AI 修正）得到的网格块转换为字段块、气泡尺寸与合并列。"""
import string
from typing import Dict, List, Optional, Tuple

from app.project import Block, make_label_text


def _unique_name(base: str, taken: set) -> str:
    index = 1
    while f"{base}{index}" in taken:
        index += 1
    name = f"{base}{index}"
    taken.add(name)
    return name


def blocks_from_detection(
    detection: dict,
    taken_names: set,
    start_number: int = 1,
) -> Tuple[List[Block], Optional[Tuple[int, int]], Dict[str, List[str]]]:
    """返回 (字段块列表, 气泡尺寸, 需要新增的合并列)。

    detection 中每个块可带 AI 修正字段：keep、kind、prefix、options、direction。
    """
    taken = set(taken_names)
    blocks: List[Block] = []
    merges: Dict[str, List[str]] = {}
    number = start_number
    digit_groups = 0
    for item in detection.get("blocks", []):
        if item.get("keep") is False or item.get("kind") == "other":
            continue
        block = Block(name="", x=item["x"], y=item["y"], w=item["w"], h=item["h"])
        direction = item.get("direction") or "horizontal"
        if item.get("kind") == "digits":
            digit_groups += 1
            block.set_type("QTYPE_INT")
            count = item["cols"]
            prefix = "roll" if digit_groups == 1 else f"n{digit_groups}_"
            block.labels = make_label_text(prefix, 1, count)
            block.name = _unique_name("准考号" if digit_groups == 1 else "数字区", taken)
            if count > 1:
                merges["Roll" if digit_groups == 1 else f"N{digit_groups}"] = [block.labels]
        else:
            options = item["options"]
            values = options if isinstance(options, list) else list(string.ascii_uppercase[: int(options)])
            count = item["rows"] if direction == "horizontal" else item["cols"]
            if direction == "horizontal" and len(values) == 4 and values == ["A", "B", "C", "D"]:
                block.set_type("QTYPE_MCQ4")
            elif direction == "horizontal" and len(values) == 5 and values == ["A", "B", "C", "D", "E"]:
                block.set_type("QTYPE_MCQ5")
            else:
                block.field_type = "__CUSTOM__"
                block.values = list(values)
                block.direction = direction
            prefix = item.get("prefix") or "q"
            block.labels = make_label_text(prefix, number, count)
            number += count
            block.name = _unique_name("字段块", taken)
        blocks.append(block)
    bubble = detection.get("bubble")
    return blocks, (tuple(bubble) if bubble else None), merges
