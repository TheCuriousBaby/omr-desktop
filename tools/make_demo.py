"""由官方样例 sample1 生成内置示例方案 assets/demo（模板、标记点、标准答案与示例答题卡）。"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.project import AnswerRow, Marking, build_evaluation, dump_json  # noqa: E402

SAMPLE = ROOT / "vendor" / "OMRChecker" / "samples" / "sample1"
TARGET = ROOT / "assets" / "demo"

ANSWERS = {
    "q1": "B", "q2": "C", "q3": "D", "q4": "A", "q5": "6", "q6": "11", "q7": "20",
    "q8": "7", "q9": "16", "q10": "B", "q11": "D", "q12": "C", "q13": "D", "q14": "A",
    "q15": "D", "q16": "B", "q17": "A", "q18": "C", "q19": "C", "q20": "D",
}


def build():
    shutil.rmtree(TARGET, ignore_errors=True)
    project = TARGET / "project"
    project.mkdir(parents=True)
    (TARGET / "sheets").mkdir()
    shutil.copyfile(SAMPLE / "template.json", project / "template.json")
    shutil.copyfile(SAMPLE / "omr_marker.jpg", project / "omr_marker.jpg")
    shutil.copyfile(SAMPLE / "MobileCamera" / "sheet1.jpg", project / "reference.jpg")
    shutil.copyfile(SAMPLE / "MobileCamera" / "sheet1.jpg", TARGET / "sheets" / "示例答题卡1.jpg")
    (project / "project.json").write_text(json.dumps({"reference": "reference.jpg"}), "utf-8")
    rows = [AnswerRow(label, answer) for label, answer in ANSWERS.items()]
    dump_json(project / "evaluation.json", build_evaluation(rows, Marking("1", "0", "0")))


if __name__ == "__main__":
    build()
