# -*- coding: utf-8 -*-
"""build_exe.py —— 把「歌词查重」插件打成能独立运行的 EXE。

    python build_exe.py            # 单文件
    python build_exe.py --onedir   # 目录版（热更新用这个）
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "歌词查重"
ENTRY = os.path.join(HERE, "dupscan", "__main__.py")
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "core"))

HIDDEN = [
    "qishui_core.api", "qishui_core.auth", "qishui_core.store",
    "qishui_core.paths", "qishui_core.context", "qishui_core.standalone",
    "qishui_core.cdp_lite", "qishui_core.launcher", "qishui_core.schedule",
    "qishui_core.updater",
]

EXCLUDE = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
           "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.Qt3DCore",
           "tkinter", "numpy", "scipy", "matplotlib"]


def main(argv):
    onedir = "--onedir" in argv
    if "--clean" in argv:
        for d in ("build", "dist"):
            shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)
        print("已清理 build/dist")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed",
           "--name", NAME, "--onedir" if onedir else "--onefile"]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for x in EXCLUDE:
        cmd += ["--exclude-module", x]
    if os.path.isdir(CORE):
        cmd += ["--paths", CORE]
    cmd += ["--add-data", "%s%s." % (os.path.join(HERE, "plugin.json"), os.pathsep)]
    cmd += ["--distpath", os.path.join(HERE, "dist"),
            "--workpath", os.path.join(HERE, "build"), ENTRY]
    print(" ".join(cmd))
    rc = subprocess.call(cmd, cwd=HERE)
    if rc == 0:
        print("\n完成：%s" % os.path.join(HERE, "dist", NAME))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
