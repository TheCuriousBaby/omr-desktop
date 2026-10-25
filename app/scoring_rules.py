"""答案表的批量填充：按题号写入答案、套用大模型解析出的评分规则。"""
import re
from typing import Dict, List, Optional, Tuple

from app.project import AnswerRow, Marking


def label_number(label: str) -> Optional[int]:
    """题号标签末尾的数字，如 q12 → 12；没有数字时返回 None。"""
    match = re.search(r"(\d+)$", label)
    return int(match.group(1)) if match else None


def format_score(value) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value).strip()


def fill_answers(rows: List[AnswerRow], answers: Dict[int, str]) -> Tuple[int, List[int]]:
    """按题号填入答案，返回 (已填题数, 答案中有但模板里没有的题号)。"""
    by_number = {label_number(r.label): r for r in rows if label_number(r.label) is not None}
    filled = 0
    unmatched = []
    for number, answer in sorted(answers.items()):
        row = by_number.get(number)
        text = str(answer).strip().upper().replace(" ", "")
        if row is None:
            unmatched.append(number)
        elif text:
            row.answer = text
            filled += 1
    return filled, unmatched


def apply_rules(rows: List[AnswerRow], marking: Marking, reply: dict) -> Tuple[Marking, int]:
    """套用评分规则：先清空所有单题设置，再写入默认分值与各范围的特殊分值，返回 (新默认分值, 受影响题数)。"""
    default = reply.get("default") or {}
    new_marking = Marking(
        format_score(default.get("correct")) or marking.correct,
        format_score(default.get("incorrect")) or marking.incorrect,
        format_score(default.get("unmarked")) or marking.unmarked,
    )
    for row in rows:
        row.score = row.incorrect = row.partial = ""
    affected = 0
    for rule in reply.get("rules", []):
        ranges = rule.get("ranges") or []
        correct = format_score(rule.get("correct"))
        incorrect = format_score(rule.get("incorrect"))
        partial = format_score(rule.get("partial"))
        for row in rows:
            number = label_number(row.label)
            if number is None or not any(low <= number <= high for low, high in ranges):
                continue
            row.score = correct if correct and correct != new_marking.correct else ""
            row.incorrect = incorrect if incorrect and incorrect != new_marking.incorrect else ""
            row.partial = partial
            affected += 1
    return new_marking, affected
