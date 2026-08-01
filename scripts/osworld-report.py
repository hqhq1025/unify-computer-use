#!/usr/bin/env python3
"""从 results.jsonl 生成 OSWorld 跑测文档。

**数据与叙述分开**：results.jsonl 只追加、不重写；这份文档随时可以从它重新
生成。叙述可以推翻重写，数据不许。

用法：scripts/osworld-report.py > docs/osworld/README.md
"""

import json
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "docs", "osworld", "results.jsonl")


def load():
    rows = []
    if not os.path.exists(RESULTS):
        return rows
    with open(RESULTS, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def main():
    rows = load()
    by_task = defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row)

    tasks = sorted(by_task.items(), key=lambda kv: kv[1][0].get("index", 0))

    cc_rows = [r for r in rows if r.get("who") == "cc"]
    me_rows = [r for r in rows if r.get("who") == "me"]
    cc_best = defaultdict(float)
    for row in cc_rows:
        cc_best[row["task"]] = max(cc_best[row["task"]], row.get("score") or 0)
    me_best = defaultdict(float)
    for row in me_rows:
        me_best[row["task"]] = max(me_best[row["task"]], row.get("score") or 0)

    passed_cc = sum(1 for v in cc_best.values() if v >= 1.0)
    passed_me = sum(1 for v in me_best.values() if v >= 1.0)

    out = sys.stdout
    out.write("# OSWorld 全量跑测记录\n\n")
    out.write("> 这份文档由 `scripts/osworld-report.py` 从 `results.jsonl` 生成。\n"
              "> **数据只追加、不重写**；叙述可以重写，数据不许。\n\n")

    out.write("## 方法\n\n")
    out.write("每一题都走同一条流程，顺序是刻意的：\n\n")
    out.write("1. **我先用 MCP 亲手做一遍。** 真实 agent 会绕开缺陷（它会换一条路），\n"
              "   我不会——我会停下来把缺陷记下来修掉。这一步是这轮里最值钱的部分。\n")
    out.write("2. 修掉发现的链路问题。\n")
    out.write("3. **再让真实的 Claude Code 挂上这个 MCP 做一遍**，工作目录是空的临时目录，\n"
              "   Bash/Read/Write 全部禁用（否则它会绕开 GUI 直接改文件，测的就不是这条链路）。\n")
    out.write("4. 不过就修、再来，**同一题最多三次**，三次不过转下一题。\n\n")
    out.write("判分一律用 **OSWorld 官方评估器**，不自己写判据——自己写的判据会\n"
              "不自觉地照着实现来定，等于自己给自己出考卷。\n\n")

    out.write("## 汇总\n\n")
    out.write("| 项 | 值 |\n|---|---|\n")
    out.write("| 已跑题数 | **{}** / 369 |\n".format(len(tasks)))
    out.write("| 我手工通过 | {} / {} |\n".format(passed_me, len(me_best)))
    out.write("| cc 通过 | **{} / {}** |\n".format(passed_cc, len(cc_best)))
    if cc_rows:
        steps = [r["steps"] for r in cc_rows if r.get("steps")]
        toks = [r["observation_tokens"] for r in cc_rows if r.get("observation_tokens")]
        secs = [r["seconds"] for r in cc_rows if r.get("seconds")]
        if steps:
            out.write("| cc 平均步数 | {:.1f} |\n".format(sum(steps) / len(steps)))
        if toks:
            out.write("| cc 平均观测 token | {:.0f} |\n".format(sum(toks) / len(toks)))
        if secs:
            out.write("| cc 平均用时 | {:.0f}s |\n".format(sum(secs) / len(secs)))
        sem = sum(r.get("semantic") or 0 for r in cc_rows)
        syn = sum(r.get("synthesis") or 0 for r in cc_rows)
        if sem + syn:
            out.write("| 执行轴 a11y 占比 | {:.0f}% （{}/{}）|\n".format(
                100.0 * sem / (sem + syn), sem, sem + syn))
    # **按口径分开报。**
    #
    # 从第 70 题起 Bash 是开着的，而开了 Bash 之后通过率不再单纯反映这条 MCP
    # 链路的能力——第 70 题当场证明了：13 步里 11 步是 Bash，cc 用 headless
    # GIMP 跑批处理，整条桌面链路没碰。把两种口径的数字合成一个平均值，
    # 等于把两个不同的实验说成一个。
    # 缺 bash 字段的是第 70 题之前写下的记录——那时字段还不存在，而 Bash
    # 确实是关着的。按事实归到"关闭"一侧，不要因为字段缺失就把它们丢掉。
    with_bash = [r for r in cc_rows if r.get("bash")]
    without = [r for r in cc_rows if not r.get("bash")]
    if with_bash and without:
        out.write("\n### 两种口径要分开看\n\n")
        out.write("从第 70 题起 Bash 是开着的。开了 Bash 之后通过率**不再单纯反映**\n"
                  "这条 MCP 链路的能力——有些题可以完全绕开桌面用 shell 做完，\n"
                  "第 70 题就是这样（13 步里 11 步是 Bash）。\n\n")
        out.write("| 口径 | 题数 | 通过 | 平均步数 |\n|---|---|---|---|\n")
        for name, group in (("Bash 关闭（纯链路）", without), ("Bash 打开", with_bash)):
            tasks_in = {}
            for row in group:
                tasks_in[row["task"]] = max(tasks_in.get(row["task"], 0.0),
                                            row.get("score") or 0)
            steps_in = [r["steps"] for r in group if r.get("steps")]
            out.write("| {} | {} | {} | {} |\n".format(
                name, len(tasks_in),
                sum(1 for v in tasks_in.values() if v >= 1.0),
                "{:.1f}".format(sum(steps_in) / len(steps_in)) if steps_in else "—"))
    out.write("\n")

    # 失败成因分类。**没有这一段，数字会被误读**：cc 未通过的题里，
    # 目前 5 道有 4 道的根因不在模型也不在链路，而在环境或题目本身
    # （站点地理路由、评估器选择器照旧版 DOM 写、站点结果页不渲染、
    # 官方 setup 用 CDP 关标签导致"最近关闭"里没有它）。
    # 把这些和真正的模型失败混在一起报，等于把别人的账记到自己头上，
    # 反过来也会掩盖真正该修的东西。
    failed = []
    for task_id, attempts in tasks:
        best = cc_best.get(task_id)
        if best is None or best >= 1.0:
            continue
        notes = " ".join((a.get("note") or "") for a in attempts)
        first = attempts[0]
        if "地理路由" in notes or "跳转到" in notes:
            kind = "环境：站点地理路由"
        elif "旧版" in notes or "xpath" in notes.lower():
            kind = "题目：评估器选择器过时"
        elif "不渲染" in notes:
            kind = "站点：结果页不渲染"
        elif "最近关闭" in notes:
            kind = "环境：官方 setup 用 CDP 关标签"
        else:
            kind = "未归类（可能是模型或链路）"
        failed.append((first.get("index"), kind, (first.get("instruction") or "")[:46]))
    if failed:
        out.write("## cc 未通过的题，成因分类\n\n")
        out.write("> 不分类就会把环境与题目的账记到模型头上，也会掩盖真正该修的东西。\n\n")
        out.write("| # | 成因 | 题目 |\n|---|---|---|\n")
        for index, kind, inst in sorted(failed):
            out.write("| {} | {} | {} |\n".format(index, kind, inst.replace("|", "/")))
        out.write("\n")

    out.write("## 逐题\n\n")
    out.write("| # | 应用 | 题目 | 我 | cc | 步数 | 观测 token | 用时 |\n")
    out.write("|---|---|---|---|---|---|---|---|\n")
    for task_id, attempts in tasks:
        first = attempts[0]
        mine = me_best.get(task_id)
        theirs = cc_best.get(task_id)
        best_cc = None
        for row in attempts:
            if row.get("who") == "cc" and (row.get("score") or 0) == theirs:
                best_cc = row
                break

        def mark(value):
            if value is None:
                return "—"
            return "✅" if value >= 1.0 else "✗ {:.1f}".format(value)

        out.write("| {} | {} | {} | {} | {} | {} | {} | {} |\n".format(
            first.get("index", "?"), first.get("app", "?"),
            (first.get("instruction") or "")[:52].replace("|", "/"),
            mark(mine), mark(theirs),
            (best_cc or {}).get("steps", "—"),
            (best_cc or {}).get("observation_tokens", "—"),
            "{}s".format((best_cc or {}).get("seconds", "—"))
            if best_cc and best_cc.get("seconds") else "—",
        ))
    out.write("\n")

    notes = [r for r in rows if r.get("note")]
    if notes:
        out.write("## 每题的过程记录\n\n")
        current = None
        for row in sorted(notes, key=lambda r: (r.get("index", 0), r.get("at", ""))):
            if row.get("index") != current:
                current = row.get("index")
                out.write("### 第 {} 题 · {}\n\n".format(current, row.get("task", "")[:8]))
                out.write("> {}\n\n".format(row.get("instruction", "")))
            out.write("- **{}**（第 {} 次，{}）：{}\n".format(
                "我手工" if row.get("who") == "me" else "cc",
                row.get("attempt", 1),
                "得分 {}".format(row.get("score")) if row.get("score") is not None else "未判分",
                row["note"]))
        out.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
