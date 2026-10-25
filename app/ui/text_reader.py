"""文字区域读取协调器：把裁出的小图交给大模型并行识别，结果逐条回传。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from app import ai_assist

MAX_WORKERS = 3


class TextReader(QObject):
    textReady = Signal(str, str, str)  # 文件名、字段名、识别出的文字
    progressChanged = Signal(int, int)  # 已完成、总数
    failed = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._total = 0
        self._done = 0
        self._reported_error = False

    def available(self) -> bool:
        return ai_assist.configured(self.settings)

    def reset(self):
        self._total = self._done = 0
        self._reported_error = False

    def pending(self) -> int:
        return self._total - self._done

    def enqueue(self, file: str, field: str, path: str):
        self._total += 1
        self.progressChanged.emit(self._done, self._total)
        self._pool.submit(self._read, file, field, path)

    def _read(self, file: str, field: str, path: str):
        text = ""
        try:
            text = ai_assist.read_text(self.settings, Path(path).read_bytes(), field)
        except Exception as error:
            if not self._reported_error:
                self._reported_error = True
                self.failed.emit(str(error))
        self._done += 1
        self.textReady.emit(file, field, text)
        self.progressChanged.emit(self._done, self._total)

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
