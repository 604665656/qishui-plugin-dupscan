# 汽水音乐创作者工具 · 歌词查重插件（dupscan）

扫描当前账号曲库歌词，按「歌词指纹」把作品分组，找出**重复发行 / 改名再发 / 高度相似**
的片段，避免同一句 hook 反复用、同一份歌词被反复生成成不同曲再换歌名。

## 这是什么

主程序「汽水音乐发布工作台」把功能拆成了独立插件，本仓库是其中之一。
它既能被 [qishui-host](https://github.com/604665656/qishui-host) 加载成一个页签，
也能单独打成 EXE 独立运行——独立运行时自带内核（`qishui_core`），不需要宿主。

移植自主程序仓库的 `dup_scan.py`（按歌词指纹分组，找出重复发行）。

## 风险分类口径

按「规范化歌词相同」分组后，每组再分成三类风险：

- **A 已重复发行**：同一段歌词里已发行 ≥ 2 首（已经发重了）。
- **B 不能再发**：组内有已发行、且这首歌还没发（再发就与已发行重复）。
- **C 内部重复**：组内都还没发（发行时同组只挑一首即可）。
- **高度相似**：规范化后前 24 字相同、但全文不同（覆盖「同一段 hook 改了结尾几个字」）。

规范化规则：去方括号/圆括号内容 → 去非中英文数字标点 → 转小写。
规范化后少于 20 字视为纯音乐/无效歌词，不参与查重。

## 能力依赖

宿主/内核需要提供的核心能力：`store`（`api_version` 2）。其余能力（`api` / `account_*`
/ `settings` / `schedule` / `accounts`）按需使用，缺失时插件降级而非崩溃。

## 用法

```bash
# 作为插件：放进 qishui-host 的 plugins/ 目录即可被加载成「工具 > 歌词查重」页签
# 独立运行界面：
python -m dupscan
# 无界面跑一次（计划任务 / CLI 用）：
python -m dupscan --task
```

页签界面：
- 「开始查重」按钮：在后台线程扫描当前账号曲库歌词（不卡界面），结果填入表格。
- 结果表格列：**风险 / 片段（前若干字）/ 次数 / 已发版本 / 示例出处**。
- 「只看」下拉：按 A / B / C / 高度相似 过滤。
- 状态行显示当前账号、端口、数据目录与最近一次扫描结论。

## 数据从哪来、写到哪

- 读歌词/曲库：优先读账号目录里的 `works_cache.json`（按 uid 隔离，走 `ctx.account_load_json`）；
  没有或过期时走 `ctx.api.work_list` 全量拉取并回写缓存（走 `ctx.account_save_json`）。
- 写缓存：只落在账号目录（`ctx.account_path`），**绝不往任意路径写文件**。
- 不 import 任何主程序模块（`account_store` / `api_client` / `datapath` / `cdp_lite` 等），
  只依赖 `ctx` 和标准库 + `qishui_core`（由宿主/内核提供）。

## 打包成独立 EXE

```bash
pip install pyinstaller
python build_exe.py --onedir
```

## 测试

```bash
# 冒烟测试（不需要浏览器/网络/真账号）
PYTHONPATH=core:plugins/dupscan python plugins/dupscan/tests/smoke.py
# 字节码编译检查
python -m compileall -q plugins/dupscan/dupscan
```

## 实现状态

- **Bundled vs Stub**：完整实现（engine.py 全量移植，plugin.py 提供完整页签界面 + 无界面入口），
  非 stub。
- **完整 vs 受限**：完整。受限点仅来自「无浏览器/无网络」环境——
  - 拉不到曲库时 `load_works` 捕获异常降级为空列表，`scan()` 仍返回 `ok=True` 的空结果，**不崩溃**；
  - 无 GUI 环境（计划任务 / `--task`）走 `run_task()`，同样不依赖界面。
- **冒烟结果**：9 通过 / 0 失败 / 0 跳过（含插件导入、setup/build 契约、ctx 生命周期、数据根可读）。
  `compileall` 干净（rc=0）。

## 开源协议

MIT —— 与整套工具一致，每个插件独立开源、独立发版。
