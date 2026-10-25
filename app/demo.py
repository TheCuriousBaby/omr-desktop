"""内置示例方案：首次启动且没有任何方案时，安装示例方案与示例答题卡。"""
import shutil
from pathlib import Path

from app.paths import APP_DIR
from app.project import list_projects

DEMO_NAME = "示例方案"


def install_demo(project_root, state_dir, sheets_dir) -> bool:
    """安装示例方案，返回是否执行了安装。

    @param project_root 方案目录
    @param state_dir 存放“已安装”标记的目录
    @param sheets_dir 示例答题卡的放置目录
    """
    marker = Path(state_dir) / "demo_installed"
    source = APP_DIR / "assets" / "demo"
    if marker.exists() or not source.exists() or list_projects(project_root):
        return False
    target = Path(project_root) / DEMO_NAME
    if not target.exists():
        shutil.copytree(source / "project", target)
    sheets_dir = Path(sheets_dir)
    if not sheets_dir.exists():
        shutil.copytree(source / "sheets", sheets_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", "utf-8")
    return True
