"""运行路径解析：源码运行与 PyInstaller 打包两种场景下的资源位置。"""
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
VENDOR_DIR = APP_DIR / "vendor" / "OMRChecker"


def engine_command(*args):
    """返回启动引擎子进程的完整命令行（程序路径 + 参数）。"""
    if FROZEN:
        return [sys.executable, "--engine", *args]
    return [sys.executable, "-m", "app.engine_runner", *args]
