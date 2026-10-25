"""OMRChecker 引擎的子进程入口：提供 run（批量阅卷）、preview（预处理预览）、check（模板校验）三个命令。

进程标准输出中以 @@ 开头的行为结构化事件，其余为引擎日志：
  @@FILE\t<文件名>             开始处理一张图片
  @@DONE\t<文件名>\t<状态>      状态为 ok / multi / error
  @@RESULT\t<json>             命令的最终结果
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def _prepare_env():
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ["COLUMNS"] = "400"
    os.environ["NO_COLOR"] = "1"
    os.environ["TERM"] = "dumb"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from app.paths import VENDOR_DIR

    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))


def _emit_result(payload):
    print("@@RESULT\t" + json.dumps(payload, ensure_ascii=False), flush=True)


def _patch_engine():
    """让引擎支持非 ASCII 路径、跳过读不出的图片，并把日志改为纯文本。"""
    import logging

    import cv2
    import numpy as np

    def imread(path, flags=cv2.IMREAD_COLOR):
        try:
            data = np.fromfile(str(path), dtype=np.uint8)
        except OSError:
            return None
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)

    def imwrite(path, image, params=None):
        ext = os.path.splitext(str(path))[1] or ".png"
        ok, buf = cv2.imencode(ext, image, params or [])
        if ok:
            buf.tofile(str(path))
        return ok

    cv2.imread = imread
    cv2.imwrite = imwrite

    from src.utils.image import ImageUtils

    original_load = ImageUtils.load_omr_image

    def load_omr_image(file_path, tuning_config):
        images = original_load(file_path, tuning_config)
        valid = []
        for name, image in images:
            if image is None:
                print(f"无法读取图片：{name}", flush=True)
                print(f"@@DONE\t{name}\terror", flush=True)
            else:
                valid.append((name, image))
        return valid

    ImageUtils.load_omr_image = staticmethod(load_omr_image)

    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.handlers = [handler]


def _patch_progress(entry):
    original = entry._process_single_image

    def wrapped(file_path, img_name, *args, **kwargs):
        print(f"@@FILE\t{img_name}", flush=True)
        status = "error"
        try:
            ret = original(file_path, img_name, *args, **kwargs)
            status = "error" if ret is None else ("multi" if ret else "ok")
            return ret
        finally:
            print(f"@@DONE\t{img_name}\t{status}", flush=True)

    entry._process_single_image = wrapped


def cmd_run(args):
    from src import entry
    from src.evaluation import EvaluationConfig
    from src.template import Template
    from src.utils.file import Paths, setup_dirs_for_paths, setup_outputs_for_template
    from src.utils.parsing import open_config_with_defaults

    project_dir = Path(args.project)
    config_path = Path(args.config)
    tuning_config = open_config_with_defaults(config_path)
    template = Template(project_dir / "template.json", tuning_config)

    evaluation_config = None
    evaluation_path = project_dir / "evaluation.json"
    if evaluation_path.exists() and not args.noeval:
        evaluation_config = EvaluationConfig(
            project_dir, evaluation_path, template, tuning_config
        )

    files = [
        Path(line.strip())
        for line in Path(args.files).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    paths = Paths(Path(args.output))
    setup_dirs_for_paths(paths)
    outputs_namespace = setup_outputs_for_template(paths, template)

    _patch_progress(entry)
    entry.process_files(
        files, template, tuning_config, evaluation_config, outputs_namespace
    )
    _emit_result({"ok": True})


def _load_first_image(path, tuning_config):
    from src.utils.image import ImageUtils

    images = ImageUtils.load_omr_image(Path(path), tuning_config)
    if not images:
        raise Exception(f"无法读取图片：{path}")
    return images[0]


def _preprocessed_page(args):
    """用方案的预处理链处理图片，返回 (缩放到 pageDimensions 的灰度图, 页面宽, 页面高)；失败时返回 None。"""
    from src.template import Template
    from src.utils.image import ImageUtils
    from src.utils.parsing import open_config_with_defaults

    project_dir = Path(args.project)
    tuning_config = open_config_with_defaults(Path(args.config))
    template_json = json.loads((project_dir / "template.json").read_text("utf-8"))
    trimmed = {
        "pageDimensions": template_json["pageDimensions"],
        "bubbleDimensions": template_json["bubbleDimensions"],
        "preProcessors": template_json.get("preProcessors", []),
        "fieldBlocks": {},
    }
    trimmed_path = project_dir / ".preview_template.json"
    trimmed_path.write_text(json.dumps(trimmed, ensure_ascii=False), "utf-8")
    try:
        template = Template(trimmed_path, tuning_config)
    finally:
        trimmed_path.unlink(missing_ok=True)

    name, image = _load_first_image(args.image, tuning_config)
    processed = template.image_instance_ops.apply_preprocessors(name, image, template)
    if processed is None:
        return None
    page_w, page_h = template.page_dimensions
    return ImageUtils.resize_util(processed, page_w, page_h), page_w, page_h


def cmd_preview(args):
    """输出预处理并缩放到 pageDimensions 的 PNG。"""
    import cv2

    page = _preprocessed_page(args)
    if page is None:
        _emit_result({"ok": False, "error": "预处理失败：未能定位页面或标记点"})
        return
    image, page_w, page_h = page
    cv2.imencode(".png", image)[1].tofile(args.out)
    _emit_result({"ok": True, "width": page_w, "height": page_h})


def cmd_detect(args):
    """在预处理后的页面上自动识别气泡网格，结果写入 --out 指定的 JSON 文件。"""
    from app.layout_detect import detect_layout

    page = _preprocessed_page(args)
    if page is None:
        _emit_result({"ok": False, "error": "预处理失败：未能定位页面或标记点"})
        return
    image, page_w, page_h = page
    result = detect_layout(image)
    Path(args.out).write_text(json.dumps(result), "utf-8")
    _emit_result({"ok": True, "blocks": len(result["blocks"]), "width": page_w, "height": page_h})


def cmd_check(args):
    """按完整模板构造 Template，用于发现字段重叠、越界等问题。"""
    from src.template import Template
    from src.utils.parsing import open_config_with_defaults

    project_dir = Path(args.project)
    tuning_config = open_config_with_defaults(Path(args.config))
    template = Template(project_dir / "template.json", tuning_config)
    _emit_result(
        {
            "ok": True,
            "columns": list(template.output_columns),
            "fields": len(template.all_parsed_labels),
        }
    )


COMMANDS = {"run": cmd_run, "preview": cmd_preview, "detect": cmd_detect, "check": cmd_check}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--files")
    parser.add_argument("--output")
    parser.add_argument("--image")
    parser.add_argument("--out")
    parser.add_argument("--noeval")
    args = parser.parse_args(argv)

    _prepare_env()
    try:
        _patch_engine()
        COMMANDS[args.command](args)
    except SystemExit as exit_error:
        _emit_result({"ok": False, "error": f"引擎异常退出（{exit_error.code}）"})
        return 1
    except Exception as error:
        traceback.print_exc()
        _emit_result({"ok": False, "error": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
