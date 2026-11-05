# -*- coding: utf-8 -*-
"""歌词查重 插件冒烟测试：不需要浏览器 / 网络 / 真账号 / 显示服务。

用法：python tests/smoke.py      （脚本自会把 core 与本插件加入 sys.path）
"""
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))          # 插件仓库根
PROJ = os.path.abspath(os.path.join(ROOT, "..", ".."))    # qishui-toolkit
CORE = os.path.join(PROJ, "core")
for p in (CORE, ROOT):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

# 冒烟不许污染真实数据：每个进程用独立临时数据根
os.environ.setdefault("QISHUI_HOME", __import__("tempfile").mkdtemp(prefix="qs-smoke-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = "dupscan"
# 类名/包名一律以 plugin.json 的 entry 为准（coldstart 是 ColdStartPlugin 这种
# 大小写不规则的名字，硬编码必错）
try:
    _M = json.load(io.open(os.path.join(ROOT, "plugin.json"), encoding="utf-8"))
    _ENTRY = _M.get("entry") or ""
    PKG = _ENTRY.split(":")[0].split(".")[0] or PKG
    CLS = _ENTRY.split(":")[-1] or "DupscanPlugin"
except Exception:
    CLS = "DupscanPlugin"

ok = 0
skip = 0
fail = 0
_msgs = []


def check(name, cond, got=""):
    global ok, fail
    if cond:
        ok += 1
        _msgs.append("  ok  %s" % name)
    else:
        fail += 1
        _msgs.append("  --  %s  %s" % (name, got))


def skip_case(name, why):
    global skip
    skip += 1
    _msgs.append("  ..  %s（跳过：%s）" % (name, why))


def test_manifest():
    p = os.path.join(ROOT, "plugin.json")
    try:
        m = json.load(io.open(p, encoding="utf-8"))
    except Exception as e:
        check("plugin.json 可解析", False, str(e))
        return None
    check("id 字段存在", bool(m.get("id")), m.get("id"))
    check("entry 指向 Plugin 类", "Plugin" in (m.get("entry") or ""), m.get("entry"))
    check("api_version <= 2", (m.get("api_version") or 0) <= 2, m.get("api_version"))
    check("needs 是列表", isinstance(m.get("needs"), list))
    return m


def test_plugin(m):
    if not m:
        return None
    try:
        module = __import__(PKG + ".plugin", fromlist=[CLS])
    except Exception as e:
        check("导入 %s.plugin" % PKG, False, "%s: %s" % (type(e).__name__, e))
        return None
    klass = getattr(module, CLS, None)
    check("插件类存在", klass is not None, CLS)
    if klass is None:
        return None
    check("有 setup 方法", hasattr(klass, "setup"))
    check("有 build 方法", hasattr(klass, "build"))
    check("有 run_task 方法", hasattr(klass, "run_task"))
    return klass


def test_no_main_import():
    """硬规则：插件不得 import 主程序模块。"""
    import ast
    banned = {"account_store", "api_client", "datapath", "cdp_lite",
              "music_studio_app", "gui_app", "update_all", "prompt_composer",
              "song_review", "publish_core", "resident_runner", "selftest"}
    bad = []
    pkgdir = os.path.join(ROOT, PKG)
    for fn in os.listdir(pkgdir) if os.path.isdir(pkgdir) else []:
        if not fn.endswith(".py"):
            continue
        # *_src.py 是「离线 HTML 生成器」等源码资源，插件运行时不 import 它
        # （board/dashboard_src.py 里带的是生成脚本自己的可选依赖）。
        if fn.endswith("_src.py"):
            continue
        try:
            tree = ast.parse(io.open(os.path.join(pkgdir, fn), encoding="utf-8").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in banned:
                        bad.append("%s:%s" % (fn, a.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    bad.append("%s:%s" % (fn, node.module))
    check("未 import 主程序模块", not bad, ",".join(bad[:4]))


def test_lifecycle(klass):
    if klass is None:
        return
    try:
        from qishui_core import standalone as SA
    except Exception as e:
        skip_case("无界面生命周期", "qishui_core 不可用：%s" % e)
        return
    host = None
    try:
        ctx, host = SA.make_ctx("dupscan", SA.load_meta(), with_scheduler=False)
        check("构造 ctx", ctx is not None)
        from qishui_core import paths
        check("数据根可读", bool(paths.data_root()))
        obj = klass()
        check("插件可实例化", True)
        obj.setup(ctx)
        check("setup(ctx) 不抛", True)
        # 深度执行默认关闭：部分插件的 run_task 会真抓网络 / 起浏览器，
        # 冒烟测试不该联网、不该卡住。要验真实任务，设 QISHUI_SMOKE_DEEP=1。
        if os.environ.get("QISHUI_SMOKE_DEEP") == "1":
            try:
                obj.run_task()
                check("run_task 不抛", True)
            except Exception as e:
                check("run_task 不抛", False, "%s: %s" % (type(e).__name__, e))
        else:
            skip_case("run_task 深度执行", "设 QISHUI_SMOKE_DEEP=1 才跑（默认不联网）")
        try:
            s = obj.summary() if hasattr(obj, "summary") else None
            check("summary 不抛", True)
        except Exception as e:
            check("summary 不抛", False, "%s: %s" % (type(e).__name__, e))
        if hasattr(obj, "on_unload"):
            try:
                obj.on_unload()
            except Exception:
                pass
    except Exception as e:
        check("无界面生命周期", False, "%s: %s" % (type(e).__name__, e))
        traceback.print_exc()
    finally:
        if host is not None:
            try:
                host.stop()
            except Exception:
                pass


def main():
    m = test_manifest()
    klass = test_plugin(m)
    test_no_main_import()
    test_lifecycle(klass)
    print("\n".join(_msgs))
    print("\n全部检查：%d 通过 / %d 失败 / %d 跳过" % (ok, fail, skip))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
