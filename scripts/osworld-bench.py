#!/usr/bin/env python3
"""按顺序跑 OSWorld 全部题目，每题都走"我先做 → 修 → cc 做 → 修 → 再做"。

用法：
  scripts/osworld-bench.py list                     # 列出题目顺序与状态
  scripts/osworld-bench.py deploy <id|序号>          # 只布置环境，供我手工用 MCP 做
  scripts/osworld-bench.py score  <id|序号>          # 只判分（不改环境）
  scripts/osworld-bench.py agent  <id|序号> [--budget 3]  # 起一个真实 cc 跑
  scripts/osworld-bench.py record <id|序号> --who me --score 1.0 --note "…"

为什么把"布置/判分/跑 agent"拆成三个子命令，而不是一条龙：
这一轮的流程要求**我先自己用 MCP 把题做一遍**，在链路上找问题。一条龙的脚本
没法在中间停下来让人接管，而"停下来自己做"恰恰是这一轮最值钱的部分——真实
agent 会绕开缺陷（它会换一条路），我不会，我会把缺陷记下来修掉。

结果写进 results.jsonl，文档由 osworld-report.py 从它生成。**数据与叙述分开**：
叙述可以重写，数据不许重写。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osworld_local as local  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSWORLD = local.OSWORLD_ROOT
RESULTS = os.path.join(REPO, "docs", "osworld", "results.jsonl")
DEFAULT_BIN = os.path.join(REPO, "dist", "linux", "amd64", "open-computer-use")
SERVER_NAME = "ocu"

# 这些内建工具必须禁掉，否则 agent 会绕开 GUI 直接改文件/跑脚本，
# 测的就不是这条链路了。
DISALLOWED = [
    "Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "KillShell", "BashOutput",
]


def task_order():
    """官方 test_all.json 的顺序，这是"第一题"的唯一定义。"""
    with open(os.path.join(OSWORLD, "evaluation_examples", "test_all.json"),
              encoding="utf-8") as handle:
        groups = json.load(handle)
    order = []
    for app, ids in groups.items():
        for task_id in ids:
            order.append((app, task_id))
    return order


def load_task(app, task_id):
    path = os.path.join(OSWORLD, "evaluation_examples", "examples", app,
                        task_id + ".json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolve(selector):
    order = task_order()
    if selector.isdigit():
        index = int(selector) - 1
        if not (0 <= index < len(order)):
            raise SystemExit("序号超出范围 1..{}".format(len(order)))
        return index, order[index]
    for index, (app, task_id) in enumerate(order):
        if task_id.startswith(selector):
            return index, (app, task_id)
    raise SystemExit("找不到题目 {!r}".format(selector))


def read_results():
    if not os.path.exists(RESULTS):
        return []
    rows = []
    with open(RESULTS, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def append_result(row):
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def cmd_list(args):
    order = task_order()
    rows = read_results()
    done = {}
    for row in rows:
        done.setdefault(row["task"], []).append(row)
    print("共 {} 题".format(len(order)))
    limit = args.limit or 30
    for index, (app, task_id) in enumerate(order[:limit], start=1):
        attempts = done.get(task_id, [])
        best = max((a.get("score") or 0) for a in attempts) if attempts else None
        mark = "—" if best is None else ("✅" if best >= 1.0 else "✗{:.1f}".format(best))
        print("{:>4}  {:<20} {:<40} {} ({} 次)".format(
            index, app, task_id, mark, len(attempts)))


def cmd_deploy(args):
    index, (app, task_id) = resolve(args.task)
    task = load_task(app, task_id)
    print("=" * 70)
    print("第 {} 题  [{}]  {}".format(index + 1, app, task_id))
    print("指令: {}".format(task["instruction"]))
    print("=" * 70)
    if not (task.get("config") or []):
        local.ensure_app_running(app)
    if local._touches_chrome(task):
        local.snapshot_state(task)
        local.clean_chrome_session()
    ready, skipped = local.apply_config(task)
    if skipped:
        print("\n⚠️ 以下 config 步骤没有执行: {}".format(", ".join(skipped)))
        print("   这道题的环境**不完整**，任何失败都不能算模型的账。")
    print("\n环境已布置。现在可以用 MCP 手工做这道题。")
    print("做完后判分: scripts/osworld-bench.py score {}".format(task_id[:8]))
    return 0 if ready else 3


def cmd_score(args):
    index, (app, task_id) = resolve(args.task)
    task = load_task(app, task_id)
    score, detail = local.evaluate(task)
    print("第 {} 题 [{}] {}".format(index + 1, app, task_id))
    print("得分: {}".format(score))
    print("明细: {}".format(detail))
    if args.record:
        append_result({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "index": index + 1, "app": app, "task": task_id,
            "instruction": task["instruction"],
            "who": args.who, "attempt": args.attempt,
            "score": score, "detail": detail, "note": args.note or "",
        })
        print("已记入 {}".format(RESULTS))
    return 0


def register_mcp(binary, workdir):
    os.makedirs(workdir, exist_ok=True)
    result = subprocess.run(["claude", "mcp", "add", SERVER_NAME, "--", binary, "mcp"],
                            cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in (result.stdout + result.stderr):
        raise RuntimeError("注册 MCP 失败: {}{}".format(result.stdout, result.stderr))
    check = subprocess.run(["claude", "mcp", "list"], cwd=workdir,
                           capture_output=True, text=True)
    if "Connected" not in check.stdout:
        raise RuntimeError("MCP 没连上:\n" + check.stdout + check.stderr)


def summarize(path):
    calls, semantic, synthesis, chars = [], 0, 0, 0
    usage, final = {}, ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        calls.append(block.get("name", "?"))
                usage = event.get("message", {}).get("usage", usage) or usage
            elif event.get("type") == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") != "tool_result":
                        continue
                    content = block.get("content")
                    text = ""
                    if isinstance(content, list):
                        text = "".join(c.get("text", "") for c in content
                                       if isinstance(c, dict))
                    elif isinstance(content, str):
                        text = content
                    chars += len(text)
                    semantic += text.count("[semantic]")
                    synthesis += text.count("[synthesis]")
            elif event.get("type") == "result":
                final = event.get("result") or ""
                usage = event.get("usage", usage) or usage
    return {
        "steps": len(calls), "tools": calls,
        "semantic": semantic, "synthesis": synthesis,
        "observation_tokens": chars // 4, "usage": usage, "final": final,
    }


# 关键词表**必须靠实测扩充，不能靠想象**。第 11 题上它就漏过一次：
# cc 的自述开头是 "I can't complete this, for two separate reasons."，
# 而表里只有 "can't do"——判据给出假阴性，把一次正确的拒绝记成了失败。
# 仪器给假阴性比没有仪器更糟，这条在本仓库已经犯过四次。
REFUSAL_MARKERS = (
    "not possible", "no longer possible", "isn't possible", "is not possible",
    "cannot be done", "can't be done", "cannot do", "can't do",
    "cannot complete", "can't complete", "unable to complete",
    "cannot be completed", "can't be completed",
    "infeasible", "does not exist", "doesn't exist", "no such",
    "was removed", "has been removed", "not available", "no longer available",
    "unable to", "there is no", "there's no", "not supported",
    "isn't supported", "is not supported", "no way to",
)


def judge_refusal(final_text):
    """infeasible 题：agent 有没有如实说"这做不到"。

    官方用 agent 发出的 FAIL 信号判分。我们的 agent 说人话，所以只能读它的
    自述。关键词判据天然不精确，**所以自述原文会整段存进 results.jsonl**——
    读的人可以自己复核并推翻这个判断。把一个不精确的判据藏起来才是问题，
    用它但留下证据不是。
    """
    text = (final_text or "").lower()
    if not text.strip():
        return 0.0, "agent 没有留下自述，无法判断它是否拒绝了"
    hits = [m for m in REFUSAL_MARKERS if m in text]
    if hits:
        return 1.0, "自述里出现了拒绝表述：{}".format(", ".join(hits[:3]))
    return 0.0, "自述里没有出现拒绝表述——它可能以为自己做成了"


def cmd_agent(args):
    index, (app, task_id) = resolve(args.task)
    task = load_task(app, task_id)
    print("第 {} 题 [{}] {}".format(index + 1, app, task_id))
    print("指令: {}".format(task["instruction"]))

    if not args.skip_config:
        if not (task.get("config") or []):
            local.ensure_app_running(app)
        if local._touches_chrome(task):
            local.snapshot_state(task)
            local.clean_chrome_session()
        ready, skipped = local.apply_config(task)
        if skipped:
            print("⚠️ 未执行的 config: {}".format(", ".join(skipped)))
        time.sleep(5)

    workdir = "/tmp/ocu-agent-run"
    shutil.rmtree(workdir, ignore_errors=True)
    register_mcp(args.binary, workdir)
    transcript = "/tmp/osworld-agent-{}.jsonl".format(task_id[:8])
    trace = "/tmp/osworld-trace-{}.jsonl".format(task_id[:8])
    environ = dict(os.environ, OPEN_COMPUTER_USE_TRACE_FILE=trace)

    command = [
        "claude", "-p", task["instruction"],
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", str(args.budget),
        "--disallowedTools", *DISALLOWED,
    ]
    started = time.time()
    with open(transcript, "w", encoding="utf-8") as handle:
        subprocess.run(command, cwd=workdir, stdout=handle,
                       stderr=subprocess.DEVNULL, text=True, env=environ)
    elapsed = time.time() - started

    stats = summarize(transcript)
    funcs = task.get("evaluator", {}).get("func")
    funcs = [funcs] if isinstance(funcs, str) else (funcs or [])
    if funcs == ["infeasible"]:
        # 官方的 `infeasible` 判据是空函数——它靠 agent 输出 FAIL 来判分。
        # 我们的 agent 不发 FAIL，它说人话，所以判据落在**它有没有拒绝**上。
        #
        # 这条判据必须能被人复核，所以整段自述会原样存进 results.jsonl：
        # 用关键词判"拒绝"天然不精确，把原文留下，读的人可以自己推翻我。
        score, detail = judge_refusal(stats["final"])
    else:
        score, detail = local.evaluate(task)
    print("\n步数 {}  观测 token≈{}  用时 {:.0f}s".format(
        stats["steps"], stats["observation_tokens"], elapsed))
    print("得分 {}   明细 {}".format(score, detail))

    append_result({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index": index + 1, "app": app, "task": task_id,
        "instruction": task["instruction"],
        "who": "cc", "attempt": args.attempt,
        "score": score, "detail": detail,
        "steps": stats["steps"], "tools": stats["tools"],
        "observation_tokens": stats["observation_tokens"],
        "semantic": stats["semantic"], "synthesis": stats["synthesis"],
        "seconds": round(elapsed, 1),
        "transcript": transcript, "trace": trace,
        # infeasible 题的判据是读自述，所以自述必须留档供复核。
        "final_text": stats["final"][:2000],
        "note": args.note or "",
    })
    return 0 if (score or 0) >= 1.0 else 2


def cmd_record(args):
    index, (app, task_id) = resolve(args.task)
    task = load_task(app, task_id)
    append_result({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index": index + 1, "app": app, "task": task_id,
        "instruction": task["instruction"],
        "who": args.who, "attempt": args.attempt,
        "score": args.score, "detail": args.detail or "",
        "note": args.note or "",
    })
    print("已记入 {}".format(RESULTS))
    return 0


def main():
    parser = argparse.ArgumentParser(description="按顺序跑 OSWorld")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list"); p.add_argument("--limit", type=int); p.set_defaults(fn=cmd_list)

    p = sub.add_parser("deploy"); p.add_argument("task"); p.set_defaults(fn=cmd_deploy)

    p = sub.add_parser("score")
    p.add_argument("task")
    p.add_argument("--record", action="store_true")
    p.add_argument("--who", default="me")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("agent")
    p.add_argument("task")
    p.add_argument("--binary", default=DEFAULT_BIN)
    p.add_argument("--budget", type=float, default=3.0)
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--skip-config", action="store_true")
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_agent)

    p = sub.add_parser("record")
    p.add_argument("task")
    p.add_argument("--who", default="me")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--score", type=float)
    p.add_argument("--detail", default="")
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_record)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
