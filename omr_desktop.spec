# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：生成 OMR阅卷助手 的单目录发行版。"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = []
for package in ("rich", "screeninfo", "fitz", "pymupdf", "deepmerge", "dotmap", "jsonschema", "jsonschema_specifications", "referencing", "openpyxl", "segno"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass
hiddenimports += ["matplotlib.backends.backend_agg", "cv2", "numpy", "pandas"]

datas = [("assets", "assets"), ("app/web", "app/web"), ("vendor/OMRChecker/src", "vendor/OMRChecker/src"), ("vendor/OMRChecker/LICENSE", "vendor/OMRChecker")]
for package in ("jsonschema", "jsonschema_specifications", "referencing"):
    datas += collect_data_files(package)

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "onnxruntime", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OMR阅卷助手",
    console=False,
    icon="assets/icon.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OMR阅卷助手")
