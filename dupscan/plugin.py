# -*- coding: utf-8 -*-
"""歌词查重 插件：页签 + 无界面任务 共用同一个类。

移植自主程序仓库的 ``dup_scan.py``（按歌词指纹把作品分组，找出重复/改名再发）。

本插件只认 ``ctx``，不 import 任何主程序模块；读歌词走 ``ctx.account_load_json``
（曲库缓存落在账号目录，按 uid 隔离），拉曲库走 ``ctx.api.work_list``，扫描逻辑
全部在 ``engine.py`` 里，UI 与无界面入口共用同一套返回结构。

能力
----
- build(ctx)      → QWidget：开始查重按钮 + 结果表格（片段/次数/示例出处）+ 风险筛选
- run_task(...)   → 无界面入口：扫描当前账号歌词，返回重复/相似分组（dict）
- summary()       → 最近一次扫描的一句话结论
"""
from __future__ import annotations


# 风险类别的中文名（表格/筛选用）
RISK_LABEL = {
    "A": "A 已重复发行",
    "B": "B 不能再发",
    "C": "C 内部重复",
    "相似": "高度相似",
}


class DupscanPlugin(object):
    """宿主只要求 build / setup；run_task 给计划任务用。"""

    # ================================================== 无界面初始化
    def setup(self, ctx):
        self.ctx = ctx
        self._result = None          # 最近一次 scan 的结果 dict
        self._thread = None          # 后台扫描线程（GUI 用）
        ctx.log("歌词查重插件就绪（api_version=%s）"
                % (ctx.meta.api_version if ctx.meta else "?"))

    # ================================================== 页签
    def build(self, ctx):
        self.ctx = ctx
        # PySide6 只在 build 内部 import；若整库缺失让宿主兜底。
        try:
            from PySide6.QtWidgets import QLabel
        except Exception as e:
            ctx.error("歌词查重：PySide6 不可用，无法渲染界面：%s" % e)
            return QLabel("歌词查重：界面库不可用（%s）。\n无界面任务仍可运行。" % e)

        try:
            return self._build_ui(ctx)
        except Exception as e:
            ctx.error("歌词查重：界面构建失败：%s" % e)
            import traceback
            traceback.print_exc()
            return QLabel("歌词查重：界面加载失败（%s）。\n无界面任务仍可运行。" % e)

    def _build_ui(self, ctx):
        from PySide6.QtCore import Qt, QThread, Signal
        from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QHeaderView,
                                       QLabel, QPlainTextEdit, QPushButton,
                                       QTableWidget, QTableWidgetItem, QVBoxLayout,
                                       QWidget)

        ctx.on_account_switch(lambda uid: self._refresh_status())

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        # ---- 标题 ----
        head = QHBoxLayout()
        t = QLabel("歌词查重")
        from PySide6.QtGui import QFont
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        t.setFont(f)
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)

        # ---- 状态 ----
        self.lb_stat = QLabel("…")
        self.lb_stat.setWordWrap(True)
        self.lb_stat.setStyleSheet("color:#3d434d;")
        lay.addWidget(self.lb_stat)

        self.lb_summary = QLabel("还没扫描。点「开始查重」扫描当前账号曲库歌词。")
        self.lb_summary.setWordWrap(True)
        self.lb_summary.setStyleSheet("color:#8a9099; font-size:12px;")
        lay.addWidget(self.lb_summary)

        # ---- 控制行 ----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.b_run = QPushButton("开始查重")
        self.b_run.clicked.connect(self._start_scan)
        row.addWidget(self.b_run)

        self.cb_filter = QComboBox()
        self.cb_filter.addItems(["全部", "A 已重复发行", "B 不能再发",
                                 "C 内部重复", "高度相似"])
        self.cb_filter.currentTextChanged.connect(self._apply_filter)
        row.addWidget(QLabel("只看："))
        row.addWidget(self.cb_filter)

        self.b_refresh = QPushButton("刷新状态")
        self.b_refresh.clicked.connect(self._refresh_status)
        row.addWidget(self.b_refresh)
        row.addStretch(1)
        lay.addLayout(row)

        # ---- 结果表格 ----
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["风险", "片段（前若干字）", "次数", "已发版本", "示例出处"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("font-size:12px;")
        lay.addWidget(self.table, 1)

        # ---- 进度日志 ----
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setMaximumHeight(110)
        self.logv.setStyleSheet("font-family:Consolas,monospace; font-size:11px;"
                                " color:#4a515b; background:#fafbfc;"
                                " border:1px solid #eceff4; border-radius:8px;")
        lay.addWidget(self.logv)

        # ---- 后台线程信号 ----
        self._sig = _Sig()
        self._sig.line.connect(self._append)
        self._sig.done.connect(self._on_scan_done)

        self._refresh_status()
        return w

    # ================================================== 状态
    def _refresh_status(self):
        ctx = self.ctx
        if ctx is None or getattr(self, "lb_stat", None) is None:
            return
        acc = ctx.account() or {}
        name = acc.get("name") or ctx.uid or "（未登录）"
        port = ctx.port or "自动"
        self.lb_stat.setText("当前账号：<b>%s</b>　端口 %s　数据目录 %s"
                             % (name, port, ctx.account_dir()))

    # ================================================== 扫描（GUI）
    def _start_scan(self):
        if self._thread is not None:
            self.ctx.warn("歌词查重：上一次扫描还没跑完")
            return
        self.b_run.setEnabled(False)
        self.logv.clear()
        self._append("开始扫描当前账号曲库歌词…")
        ctx = self.ctx

        def job():
            from . import engine
            try:
                r = engine.scan(ctx, refresh=False)
                self._sig.done.emit(r)
            except Exception as e:
                self._sig.line.emit("失败：%s: %s" % (type(e).__name__, e))
                import traceback
                traceback.print_exc()
                self._sig.done.emit({"ok": False, "error": str(e),
                                     "groups": [], "similar": [], "quota": []})

        import threading
        self._thread = threading.Thread(target=job, daemon=True)
        self._thread.start()

    # ================================================== 表格填充
    def _collect_rows(self, result):
        """把 scan 结果拍平成表格行列表，过滤交给调用方。"""
        rows = []
        for g in result.get("groups", []):
            rows.append((g["risk"], g["snippet"], g["count"], g["n_issued"], g["example"]))
        for s in result.get("similar", []):
            rows.append((s["risk"], s["snippet"], s["count"], s["n_issued"], s["example"]))
        return rows

    def _apply_filter(self):
        if self._result is None:
            return
        self._fill_table(self._result)

    def _fill_table(self, result):
        sel = self.cb_filter.currentText() if getattr(self, "cb_filter", None) else "全部"
        want = None
        for k, v in RISK_LABEL.items():
            if v == sel:
                want = k
        rows = self._collect_rows(result)
        if want is not None:
            rows = [r for r in rows if r[0] == want]

        self.table.setRowCount(len(rows))
        for i, (risk, snip, cnt, n_issued, example) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(risk))
            self.table.setItem(i, 1, QTableWidgetItem(snip))
            self.table.setItem(i, 2, QTableWidgetItem(str(cnt)))
            self.table.setItem(i, 3, QTableWidgetItem(str(n_issued)))
            self.table.setItem(i, 4, QTableWidgetItem(example))

    # ================================================== 回调
    def _append(self, line):
        if getattr(self, "logv", None) is not None:
            self.logv.appendPlainText(str(line))

    def _on_scan_done(self, result):
        self._thread = None
        if getattr(self, "b_run", None) is not None:
            self.b_run.setEnabled(True)
        self._result = result
        if not result.get("ok", True):
            self._append("扫描未完成：%s" % result.get("error", ""))
            self.lb_summary.setText("扫描失败：%s" % result.get("error", ""))
            return
        self._fill_table(result)
        self.lb_summary.setText(result.get("summary", ""))
        self._append("完成。" + result.get("summary", ""))

    # ================================================== 无界面任务
    def run_task(self, song_ids=None, refresh=False):
        """计划任务 / 独立运行入口：扫描当前账号歌词，返回重复分组。

        song_ids：只检查这些歌（按 SongID / TaskID / 标题片段匹配）；None=全量。
        返回 engine.scan 的 dict（含 ok / total / groups / similar / summary 等）。
        任何异常都被吞掉，返回一个 ok=False 的 dict，绝不让调用方崩。
        """
        from . import engine
        try:
            return engine.scan(self.ctx, refresh=refresh, song_ids=song_ids)
        except Exception as e:
            self.ctx.error("歌词查重 run_task 异常：%s" % e)
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e), "groups": [], "similar": [],
                    "quota": [], "total": 0, "summary": "run_task 异常"}

    def summary(self):
        r = getattr(self, "_result", None)
        if r and r.get("ok"):
            return r.get("summary", "")
        return "歌词查重：尚未扫描或上次扫描失败"

    def on_account_switch(self, uid):
        try:
            self._refresh_status()
        except Exception:
            pass

    def on_unload(self):
        try:
            self.ctx.log("歌词查重插件卸载")
        except Exception:
            pass


class _Sig(object):
    """把 Qt 信号单独抽出来，避免在非 GUI（独立/计划任务）路径上 import Qt。"""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Q(QObject):
            line = Signal(str)
            done = Signal(object)
        self._q = _Q()
        self.line = self._q.line
        self.done = self._q.done
