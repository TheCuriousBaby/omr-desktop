"""界面共享上下文：当前方案与全局设置，以及变更通知。"""
from typing import Optional

from PySide6.QtCore import QObject, Signal

from app.project import Project
from app.settings import AppSettings


class AppContext(QObject):
    projectChanged = Signal()
    settingsChanged = Signal()

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self.project: Optional[Project] = None

    def set_project(self, project: Optional[Project]):
        self.project = project
        self.projectChanged.emit()
