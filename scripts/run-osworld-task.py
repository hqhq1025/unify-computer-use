#!/usr/bin/env python3
"""让一个**真实的 Claude Code 进程**通过本仓库的 MCP 去做一个 OSWorld 任务。

和 `measure-baseline.py` 的分工要分清：那个是**测量仪器**——任务链写死、不做
决策，所以任何失败都能归因到 MCP。这个相反，是**端到端试验**：agent 自己决定
下一步做什么，失败可能来自模型也可能来自工具。两者都需要，但结论的性质不同，
不要拿这里的数字去当回归门禁。

之所以要起一个独立进程而不是开子 agent：子 agent 共享父进程的上下文和工具，
测不出"一个 agent 面对陌生桌面"的真实处境。这里刻意做了三件事来保证这一点：
  1. 工作目录是空临时目录——不让它读到本仓库的 CLAUDE.md 而变成"仓库开发者"
  2. `--strict-mcp-config` + 只留 MCP 工具，禁掉 Bash/Read/Write/Edit——
     否则它会直接用 python-pptx 改文件，那测的就不是 GUI 链路了
  3. prompt 就是 OSWorld 的 instruction 原文，不加任何提示

验收用 OSWorld 自己的评估器，不自己写一份：自己写的判据会不自觉地照着
实现来定，等于自己给自己出考卷。

用法:
  scripts/run-osworld-task.py <task.json> [--budget 3.0] [--keep-file]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")
OSWORLD_ROOT = os.environ.get("OSWORLD_ROOT", "/home/user/OSWorld")
CACHE_DIR = "/tmp/osworld-cache"
# **不能叫 computer-use**——那是 Claude Code 的保留名，`claude mcp add` 会拒绝。
# 而 `--mcp-config`（无论传文件还是 JSON 字符串）在实测里根本不生效：
# stream-json 的 init 事件里 `mcp_servers` 始终是 []，agent 一个 mcp__ 工具都
# 看不到，却**不报任何错**——它只会说"我没有这些工具"，看起来像模型的问题。
# 可行的做法是 `claude mcp add --scope local`，它按目录存进 ~/.claude.json，
# 所以注册到一个一次性目录里就不会污染用户的正常项目。
SERVER_NAME = "ocu"

# 这些内建工具必须禁掉，否则 agent 会绕开 GUI 直接改文件/跑脚本，
# 试验就变成"Claude 会不会用 python-pptx"，与要回答的问题无关。
DISALLOWED = [
    "Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "KillShell", "BashOutput",
]


def fetch(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    with urllib.request.urlopen(url, timeout=180) as response:
        with open(dest, "wb") as handle:
            shutil.copyfileobj(response, handle)
    return dest


def cached_name(url):
    return os.path.join(CACHE_DIR, url.rsplit("/", 3)[-1])


def apply_config(task):
    """走一遍 OSWorld 的 config 段：下载素材 + 打开应用。"""
    opened = []
    for step in task.get("config") or []:
        kind = step.get("type")
        params = step.get("parameters") or {}
        if kind == "download":
            for item in params.get("files") or []:
                local = fetch(item["url"], cached_name(item["url"]))
                target = item["path"]
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy(local, target)
                print("  素材就位: {}".format(target))
        elif kind == "open":
            path = params["path"]
            subprocess.Popen(
                "setsid xdg-open {} </dev/null >/dev/null 2>&1 &".format(
                    subprocess.list2cmdline([path])),
                shell=True, start_new_session=True)
            opened.append(path)
            print("  已打开: {}".format(path))
        else:
            print("  ⚠️ 跳过未实现的 config 步骤: {}".format(kind))
    if opened:
        # 应用冷启动很慢，等窗口真的出现，否则 agent 第一步就看到空桌面
        deadline = time.time() + 90
        want = os.path.basename(opened[0])
        while time.time() < deadline:
            out = subprocess.run(["wmctrl", "-l"], capture_output=True,
                                 text=True).stdout
            if any(want in line for line in out.splitlines()):
                time.sleep(5)
                return True
            time.sleep(2)
        return False
    return True


def register_mcp(binary, workdir):
    """把 MCP 注册到 workdir 这个一次性目录（scope=local，按目录隔离）。"""
    os.makedirs(workdir, exist_ok=True)
    result = subprocess.run(
        ["claude", "mcp", "add", SERVER_NAME, "--", binary, "mcp"],
        cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in (result.stdout + result.stderr):
        raise RuntimeError("注册 MCP 失败: {}{}".format(result.stdout, result.stderr))
    check = subprocess.run(["claude", "mcp", "list"], cwd=workdir,
                           capture_output=True, text=True)
    if "Connected" not in check.stdout:
        raise RuntimeError("MCP 没连上:\n" + check.stdout + check.stderr)
    print("  MCP 已注册并连通（{}）".format(workdir))


def run_agent(instruction, workdir, budget, transcript_path):
    command = [
        "claude", "-p", instruction,
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--verbose",
        "--max-budget-usd", str(budget),
        "--disallowedTools", *DISALLOWED,
    ]
    print("  工作目录: {}（空目录，避免读到本仓库的 CLAUDE.md）".format(workdir))
    started = time.time()
    with open(transcript_path, "w", encoding="utf-8") as handle:
        process = subprocess.run(command, cwd=workdir, stdout=handle,
                                 stderr=subprocess.PIPE, text=True)
    elapsed = time.time() - started
    if process.stderr:
        tail = process.stderr.strip().splitlines()[-5:]
        for line in tail:
            print("  stderr: {}".format(line[:200]))
    return elapsed


def summarize(transcript_path):
    """从 stream-json 里读出四元组的原料。"""
    calls = []
    semantic = synthesis = 0
    observation_chars = 0
    usage = {}
    final_text = ""
    with open(transcript_path, encoding="utf-8") as handle:
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
                    observation_chars += len(text)
                    semantic += text.count("[semantic]")
                    synthesis += text.count("[synthesis]")
            elif event.get("type") == "result":
                final_text = event.get("result") or ""
                usage = event.get("usage", usage) or usage
    return {
        "tool_calls": calls,
        "steps": len(calls),
        "semantic": semantic,
        "synthesis": synthesis,
        "a11y_rate": (100 * semantic // (semantic + synthesis))
                     if (semantic + synthesis) else None,
        "observation_tokens": observation_chars // 4,
        "usage": usage,
        "final_text": final_text,
    }


def evaluate(task):
    """用 OSWorld 自己的评估器判分。"""
    # 直接按文件加载 slides.py，**不要走包的 __init__**——它会连带导入 chrome
    # 评估器，那条链上需要 rapidfuzz / formulas，装不装跟本任务毫无关系。
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "osworld_slides", os.path.join(
            OSWORLD_ROOT, "desktop_env/evaluators/metrics/slides.py"))
    metrics_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics_module)

    spec = task["evaluator"]
    funcs = spec["func"]
    if isinstance(funcs, str):
        funcs = [funcs]
    expected = spec.get("expected")
    result = spec.get("result")
    if isinstance(expected, dict):
        expected = [expected]
    if isinstance(result, dict):
        result = [result]
    conj = spec.get("conj", "and")

    scores = []
    for index, name in enumerate(funcs):
        fn = getattr(metrics_module, name)
        gold_spec = expected[index]
        got_spec = result[index]
        gold = fetch(gold_spec["path"], cached_name(gold_spec["path"]))
        got = got_spec["path"]
        if not os.path.exists(got):
            scores.append(0.0)
            print("  {}: 结果文件不存在 {}".format(name, got))
            continue
        try:
            score = float(fn(got, gold, enable_debug=False))
        except Exception as error:
            score = 0.0
            print("  {} 抛异常: {}".format(name, error))
        scores.append(score)
        print("  {}[{}] -> {}".format(name, index, score))
    if not scores:
        return 0.0
    return max(scores) if conj == "or" else min(scores)


def main():
    parser = argparse.ArgumentParser(description="让真实 agent 跑一个 OSWorld 任务")
    parser.add_argument("task")
    parser.add_argument("--binary", default=DEFAULT_BIN)
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--skip-config", action="store_true",
                        help="环境已就绪时跳过下载与打开")
    parser.add_argument("--transcript", default="/tmp/osworld-agent.jsonl")
    args = parser.parse_args()

    with open(args.task, encoding="utf-8") as handle:
        task = json.load(handle)

    print("任务 {}".format(task["id"]))
    print("指令: {}".format(task["instruction"]))
    print()

    if not args.skip_config:
        print("=== 布置环境 ===")
        if not apply_config(task):
            print("环境没起来，中止")
            return 1
        print()

    workdir = "/tmp/ocu-agent-run"
    shutil.rmtree(workdir, ignore_errors=True)
    print("=== 交给 agent ===")
    register_mcp(args.binary, workdir)
    elapsed = run_agent(task["instruction"], workdir, args.budget,
                        args.transcript)
    print("  用时 {:.1f}s，轨迹写在 {}".format(elapsed, args.transcript))
    print()

    summary = summarize(args.transcript)
    print("=== agent 做了什么 ===")
    print("  步数(工具调用): {}".format(summary["steps"]))
    for name in summary["tool_calls"]:
        print("    - {}".format(name))
    print("  观测 token(粗估): {}".format(summary["observation_tokens"]))
    print("  a11y 通道占比: {}".format(
        "{}%".format(summary["a11y_rate"]) if summary["a11y_rate"] is not None
        else "n/a"))
    print("  自述: {}".format((summary["final_text"] or "")[:400]))
    print()

    print("=== OSWorld 评估器判分 ===")
    score = evaluate(task)
    print()
    print("=" * 52)
    print("  得分 {}   步数 {}   a11y {}".format(
        score, summary["steps"],
        "{}%".format(summary["a11y_rate"]) if summary["a11y_rate"] is not None
        else "n/a"))
    print("=" * 52)
    return 0 if score > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
