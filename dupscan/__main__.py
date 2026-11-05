# -*- coding: utf-8 -*-
"""歌词查重 插件的独立形态：python -m dupscan 打开界面；--task 无界面运行。"""
import io
import json
import os
import sys


def _plugin_class():
    """从 plugin.json 的 entry 取类名，避免大小写手写错。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        m = json.load(io.open(os.path.join(root, "plugin.json"), encoding="utf-8"))
        entry = m.get("entry") or ""
        mod = entry.split(":")[0].split(".")[0] or "dupscan"
        cls = entry.split(":")[-1] or "DupscanPlugin"
    except Exception:
        mod, cls = "dupscan", "DupscanPlugin"
    package = __import__(mod + ".plugin", fromlist=[cls])
    return getattr(package, cls)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    from qishui_core import standalone as SA
    PluginClass = _plugin_class()
    if "--task" in argv:
        meta = SA.load_meta()
        ctx, host = SA.make_ctx("dupscan", meta, with_scheduler=False)
        try:
            obj = PluginClass()
            obj.setup(ctx)
            r = obj.run_task()
        finally:
            host.stop()
        return 0 if (r or {}).get("ok", True) else 1
    return SA.run_plugin_window(PluginClass, "dupscan", argv=sys.argv, size=(1024, 720))


if __name__ == "__main__":
    sys.exit(main())
