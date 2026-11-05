# -*- coding: utf-8 -*-
"""歌词查重引擎。

移植自主程序仓库的 ``dup_scan.py``（按歌词指纹把作品分组，找出重复发行）。

本文件**只依赖 ctx 和标准库**，不 import 任何主程序模块：
    - 读歌词：走 ``ctx.account_load_json`` / ``ctx.account_path``（账号目录，按 uid 隔离）
    - 拉曲库：走 ``ctx.api.work_list``（qishui_core 已内置 cookie / 接口）
    - 写缓存：走 ``ctx.account_save_json``（只落在账号目录，不碰任意路径）

返回结构都是 dict，便于 UI 表格和无界面 run_task 共用同一套逻辑。
"""
import datetime
import re

# ---------------------------------------------------------------- 规范化
# 去掉方括号/圆括号里的内容（多为伴奏、版本说明），再去掉非中英文数字字符。
_SEC = re.compile(r"\[[^\]]{0,40}\]|[\(（][^\)）]{0,40}[\)）]")
_NON = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]")
MIN_LY = 20          # 规范化后少于 20 字视为纯音乐/无效歌词，不参与查重
SIM_PREFIX = 24      # 近似片段：规范化后前 N 字相同的视为「高度相似」

# 曲库缓存文件名（落在账号目录，按 uid 隔离）
CACHE_NAME = "works_cache.json"


# ---------------------------------------------------------------- 数据整形
def _slim(w):
    """把一条 work-list 原始记录收成查重需要的字段。"""
    ii = w.get("IssueInfo") or {}
    return {
        "title": (w.get("Title") or "").strip(),
        "task": str(w.get("TaskID") or ""),
        "vid": (w.get("VID") or "").strip(),
        "song": str(w.get("SongID") or ""),
        "dur": int(w.get("Duration") or 0),
        "ct": int(w.get("CreateTime") or 0),
        "lyrics": w.get("Lyrics") or "",
        "issued": bool(ii.get("Issued")),
        "issue_id": (ii.get("IssueParams") or {}).get("IssueID", ""),
    }


def norm_lyrics(s):
    """规范化歌词：去括号、去标点、转小写。"""
    if not s:
        return ""
    s = s.replace("\r", "").replace("\n", "")
    s = _SEC.sub("", s)
    s = _NON.sub("", s)
    return s.lower()


def _fmt_ts(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# ---------------------------------------------------------------- 拉取
def load_works(ctx, uid=None, refresh=False, page_size=1000):
    """读当前账号曲库作品（带歌词）。

    优先用账号目录里的缓存 ``works_cache.json``；没有或 ``refresh`` 时，
    走 ``ctx.api.work_list`` 全量拉取并回写缓存。任何一步失败都降级为空列表，
    不让调用方崩。
    """
    uid = uid or ctx.uid
    if not refresh:
        data = ctx.account_load_json(CACHE_NAME, None, uid)
        if data and data.get("works"):
            ctx.log("歌词查重：用曲库缓存 %d 首" % len(data["works"]))
            return data["works"]

    works = []
    try:
        first = ctx.api.work_list(1, page_size, 2)
        total = int(first.get("total") or 0)
        works.extend(first.get("works") or [])
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        ctx.log("歌词查重：拉取曲库 %d 首 / %d 页" % (total, pages))
        for p in range(2, pages + 1):
            r = ctx.api.work_list(p, page_size, 2)
            works.extend(r.get("works") or [])
    except Exception as e:
        ctx.warn("歌词查重：拉取曲库失败（将只分析本地缓存）：%s" % e)

    seen, out = set(), []
    for w in works:
        k = (w.get("TaskID"), w.get("Title"), w.get("CreateTime"))
        if k in seen:
            continue
        seen.add(k)
        out.append(_slim(w))

    if out:
        try:
            ctx.account_save_json(
                CACHE_NAME,
                {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "uid": uid, "total": len(out), "works": out},
                uid)
        except Exception as e:
            ctx.warn("歌词查重：写曲库缓存失败：%s" % e)
    return out


# ---------------------------------------------------------------- 分组
def build_groups(works):
    """按歌词指纹（规范化全文）分组，返回出现 >=2 次的分组。"""
    by = {}
    for w in works:
        ly = norm_lyrics(w["lyrics"])
        if len(ly) >= MIN_LY:
            by.setdefault(ly, []).append(w)
    return {ly: g for ly, g in by.items() if len(g) > 1}


def build_similar(works, prefix=SIM_PREFIX):
    """近似片段：规范化后前 ``prefix`` 字相同的归为一组（高度相似，非完全一致）。

    这是 exact 指纹分组的补充，覆盖「同一段 hook 改了结尾几个字」的情况。
    复杂度 O(n)，不 pairwise 比对。
    """
    by = {}
    for w in works:
        ly = norm_lyrics(w["lyrics"])
        if len(ly) >= MIN_LY:
            by.setdefault(ly[:prefix], []).append(w)
    out = {}
    for k, g in by.items():
        # 去掉已经能被 exact 完全命中（组内歌词全相同）的纯近似组，避免重复计数
        distinct = {norm_lyrics(x["lyrics"]) for x in g}
        if len(g) > 1 and len(distinct) > 1:
            out[k] = g
    return out


def classify(groups):
    """把重复组分成 A 已重复发行 / B 不能再发 / C 内部重复。"""
    A, B, C = [], [], []
    for ly, g in groups.items():
        issued = [w for w in g if w["issued"]]
        unissued = [w for w in g if not w["issued"]]
        if len(issued) >= 2:
            A.append((ly, g))
        if issued and unissued:
            B.append((ly, g))
        if not issued and unissued:
            C.append((ly, g))
    return A, B, C


def quota_rows(groups, quota=3):
    """按歌词统计已发行版本数，给出「配额」清单（不写文件，返回 list[dict]）。"""
    rows = []
    for ly, g in groups.items():
        issued = [w for w in g if w["issued"]]
        unissued = [w for w in g if not w["issued"]]
        if not issued:
            status = "整组未发（可挑最好的 %d 个发）" % min(quota, len(g))
            over = False
        elif len(issued) >= quota:
            status = "已发 %d 个（≥配额 %d，别再发）" % (len(issued), quota)
            over = True
        else:
            status = "已发 %d 个，还可发 %d 个" % (len(issued), quota - len(issued))
            over = False
        rows.append({
            "sample": issued[0]["title"] if issued else g[0]["title"],
            "ly_len": len(ly),
            "n_issued": len(issued),
            "n_un": len(unissued),
            "total": len(g),
            "over": over,
            "status": status,
            "issued_titles": "、".join(w["title"] for w in issued),
            "un_titles": "、".join(w["title"] for w in unissued),
        })
    rows.sort(key=lambda r: (-r["n_issued"], -r["total"]))
    return rows


# ---------------------------------------------------------------- 对外扫描
def _group_to_row(ly, g, risk):
    titles = [w["title"] for w in g]
    issued = [w for w in g if w["issued"]]
    return {
        "risk": risk,                       # A / B / C / 相似
        "lyric": ly,                        # 规范化歌词（指纹）
        "snippet": ly[:SIM_PREFIX],         # 表格里显示的片段
        "count": len(g),
        "n_issued": len(issued),
        "songs": titles,                    # 出自哪些歌
        "example": "、".join(titles[:3]) + ("…" if len(titles) > 3 else ""),
    }


def scan(ctx, uid=None, refresh=False, song_ids=None, quota=3):
    """扫描当前账号歌词，返回重复/相似片段分组。

    返回 dict（run_task / build 共用）：
        ok / total / n_with_lyrics / group_count / similar_count
        groups（精确重复，含 A/B/C 分类）/ similar（高度相似）
        quota（配额清单）/ summary（一句话结论）
    """
    uid = uid or ctx.uid
    try:
        works = load_works(ctx, uid, refresh=refresh)
    except Exception as e:
        ctx.error("歌词查重：加载曲库异常：%s" % e)
        return {"ok": False, "error": str(e), "groups": [], "similar": [],
                "quota": [], "total": 0, "n_with_lyrics": 0,
                "group_count": 0, "similar_count": 0, "summary": "加载曲库失败"}

    if song_ids:
        wanted = set(str(x) for x in song_ids)
        ws = [w for w in works
              if str(w["song"]) in wanted or str(w["task"]) in wanted
              or any(str(x) in w["title"] for x in wanted)]
        if ws:
            works = ws

    n_with = sum(1 for w in works if len(norm_lyrics(w["lyrics"])) >= MIN_LY)
    groups = build_groups(works)
    A, B, C = classify(groups)
    similar = build_similar(works)

    # 按组对象身份判定风险类别（一个组只可能落在 A/B/C 之一）。
    a_set = {id(g) for _, g in A}
    b_set = {id(g) for _, g in B}
    group_rows = []
    for ly, g in groups.items():
        if id(g) in a_set:
            risk = "A"
        elif id(g) in b_set:
            risk = "B"
        else:
            risk = "C"
        group_rows.append(_group_to_row(ly, g, risk))

    similar_rows = [_group_to_row(k, g, "相似") for k, g in similar.items()]

    qrows = quota_rows(groups, quota)

    summary = ("曲库 %d 首（含歌词 %d 首）；精确重复 %d 组（A已重发 %d / B不能再发 %d / C内部重复 %d），"
               "高度相似 %d 组" % (
                   len(works), n_with, len(groups), len(A), len(B), len(C), len(similar)))

    return {
        "ok": True,
        "total": len(works),
        "n_with_lyrics": n_with,
        "group_count": len(groups),
        "similar_count": len(similar),
        "groups": group_rows,
        "similar": similar_rows,
        "quota": qrows,
        "A": len(A), "B": len(B), "C": len(C),
        "summary": summary,
    }
