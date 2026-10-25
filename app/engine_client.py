"""在 Qt 中启动引擎子进程并解析其结构化输出。"""
import json
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from app.paths import APP_DIR, engine_command


def write_config(path, config):
    Path(path).write_text(json.dumps(config, ensure_ascii=True, indent=2), "utf-8")


class EngineJob(QObject):
    logLine = Signal(str)
    fileStarted = Signal(str)
    fileDone = Signal(str, str)
    finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._buffer = ""
        self._result: Optional[dict] = None
        self._cancelled = False

    def running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def start(self, command: str, **options):
        args = [command]
        for key, value in options.items():
            args += [f"--{key}", str(value)]
        program, *arguments = engine_command(*args)
        self._result = None
        self._buffer = ""
        self._cancelled = False
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.setWorkingDirectory(str(APP_DIR))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONWARNINGS", "ignore")
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(program, arguments)

    def cancel(self):
        if self.running():
            self._cancelled = True
            self._process.kill()

    def _read(self):
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer += data
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._handle_line(line.rstrip("\r"))

    def _handle_line(self, line: str):
        if line.startswith("@@FILE\t"):
            self.fileStarted.emit(line.split("\t", 1)[1])
        elif line.startswith("@@DONE\t"):
            _, name, status = line.split("\t")
            self.fileDone.emit(name, status)
        elif line.startswith("@@RESULT\t"):
            try:
                self._result = json.loads(line.split("\t", 1)[1])
            except ValueError:
                self._result = {"ok": False, "error": "引擎输出无法解析"}
        elif line.strip():
            self.logLine.emit(line)

    def _on_error(self, error):
        if error == QProcess.FailedToStart and self._result is None:
            self._result = {"ok": False, "error": "无法启动识别引擎"}

    def _on_finished(self, exit_code, _status):
        if self._buffer.strip():
            self._handle_line(self._buffer.rstrip("\r\n"))
            self._buffer = ""
        if self._cancelled:
            result = {"ok": False, "cancelled": True, "error": "已取消"}
        elif self._result is not None:
            result = self._result
        else:
            result = {"ok": False, "error": "识别引擎异常终止"}
        self.finished.emit(result)


def temp_path(name: str) -> Path:
    folder = Path(tempfile.gettempdir()) / "omr-desktop"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name
