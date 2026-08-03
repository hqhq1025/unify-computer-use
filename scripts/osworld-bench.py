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
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osworld_local as local  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSWORLD = local.OSWORLD_ROOT
# 结果文件可以按 worker 分开。
#
# 并行跑的时候多个进程会同时往这里追加，而单条记录带着整段自述、
# 经常超过 4KB——超过 PIPE_BUF 的 O_APPEND 写**不保证原子**，交错写会把
# 两条 JSON 拧成一行，事后谁也解析不出来。数据只追加不重写的前提是
# 每一行都完整，所以并行时每个 worker 写自己的文件，跑完再合并。
RESULTS = os.environ.get(
    "OSWORLD_RESULTS", os.path.join(REPO, "docs", "osworld", "results.jsonl"))
DEFAULT_BIN = os.path.join(REPO, "dist", "linux", "amd64", "open-computer-use")
SERVER_NAME = "ocu"

# 这些内建工具必须禁掉，否则 agent 会绕开 GUI 直接改文件/跑脚本，
# 测的就不是这条链路了。
# 禁用的内置工具。
#
# **Bash 现在是开的。** 原来它在禁用列表里，理由是"不禁的话 agent 会绕开 GUI
# 直接改文件，测的就不是这条链路"。这个理由站得住，但它测的也不是**真实的
# Claude Code**——真实用户的 Bash 一直开着，OSWorld 官方那套 agent 在虚拟机里
# 同样能开终端。而且轨迹已经证明禁不住：67 条里有 2 条，模型自己通过 GUI 打开
# GNOME 终端、往里 type_text 打 Python 代码，把终端当成一个图形应用来用。
#
# 代价要说清楚：开了 Bash 之后，**通过率不再单纯反映这条 MCP 链路的能力**，
# 因为有些题可以完全绕开桌面用 shell 做完。所以每条记录都会带上 `bash` 字段，
# 两种口径的数据可以分开算，不许混在一起报。
#
# 想跑"纯链路"口径时加 --no-bash。
DISALLOWED_BASE = [
    "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "KillShell", "BashOutput",
    # 下面这一批是**后来才发现漏掉的**。
    #
    # 从轨迹的 init 事件里读出来才知道：agent 手上有 28 个工具，
    # 除了 13 个 ocu 和 Bash，还有 Skill / Workflow / TaskCreate…/Cron…/
    # SendMessage / EnterWorktree / ScheduleWakeup / ReportFindings。
    # 我的禁用列表写的是老一批工具名，这些新的从来没进过列表。
    #
    # Workflow 能派生子 agent，Skill 能加载 deep-research / code-review
    # 这类完整流程——那就不是"一个 agent 用 MCP 操作桌面"了。
    #
    # 实测扫过全部 137 条轨迹、2203 次调用，这些**一次都没被用过**，
    # 所以已有数据没被污染。但"它没用"和"它不能用"是两回事：
    # 换台机器、换个用户配置，跑出来的可能就是另一回事，而这种差异不报错。
    "Skill", "Workflow", "SendMessage", "ReportFindings",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate",
    "CronCreate", "CronDelete", "CronList",
    "EnterWorktree", "ExitWorktree", "ScheduleWakeup",
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


# 需要真实第三方凭据的题——本机跑不了，也不该记成模型失败。
#
# 实测第 194 题：cc 把能做的都做完了（从 Thunderbird 邮件附件里提取出图片、
# 编好号），卡在"上传到 Google Drive"这一步，因为那需要真实账号。
# 三次都是同样的结果。把这种记成 0.0，等于把环境的欠缺算到模型头上。
#
# 判据里出现 googledrive getter 或 login 配置的就是这一类，实测 8 道，
# 全在 multi_apps 段：194 195 199 200 202 203 222 288。
# 判据要查 ~/.bash_history 的题——本机跑法下**天然测不了**。
#
# 这几道题的意图是"必须用命令行做，不许用 GUI"，判据去 ~/.bash_history 里
# grep 那条命令。而 cc 是通过 MCP 的 Bash 工具执行的，**非交互式 shell 不写
# history**，所以它即使完全正确地跑了
#     soffice --headless --convert-to pdf --outdir ~/Desktop *.doc
# （第 208 题轨迹里实测有这一行），判据也查不到，history 文件只有 1 字节。
#
# 官方那套 agent 用 pyautogui 往真实终端窗口里打字，那才会写进 history。
# 这是**跑法差异**，不是模型做错，也不是链路缺陷。
#
# 不去伪造 history：那等于替被测方作弊，而且会让这条判据永远说不了真话。
# 实测 4 道：190 196 207 208。
def checks_bash_history(task):
    return "bash_history" in json.dumps(task.get("evaluator") or {},
                                        ensure_ascii=False)


def needs_real_credentials(task):
    blob = json.dumps(task, ensure_ascii=False).lower()
    return "googledrive" in blob or '"login"' in blob


def cmd_deploy(args):
    index, (app, task_id) = resolve(args.task)
    task = load_task(app, task_id)
    print("=" * 70)
    print("第 {} 题  [{}]  {}".format(index + 1, app, task_id))
    print("指令: {}".format(task["instruction"]))
    print("=" * 70)
    if not (task.get("config") or []):
        local.ensure_app_running(app)
    # 先关掉上一题留下的应用。见 close_stale_apps：不关会静默耗尽内存。
    local.close_stale_apps()
    if local._touches_chrome(task):
        local.snapshot_state(task)
        local.clean_chrome_session()
    # GIMP 同理：被 pkill 掉之后它会把上次当成崩溃，下一题一开局先弹
    # "Image Recovery" 模态框，还会把一张残片图当第三个标签页留下来。
    # 见第 47 题轨迹。
    if local._touches_gimp(task):
        local.clean_gimp_session()
    # LibreOffice 同理：pkill 之后再起会弹"文档恢复"对话框，树里第一屏全是
    # 恢复列表，而题目要的那张表还没打开。libreoffice 三段共 117 道题。
    if local._touches_libreoffice(task):
        local.clean_libreoffice_session()
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


def assert_binary_is_fresh(binary):
    """二进制必须比 MCP 源码新，否则**测的是上一版**。

    这条检查是踩了大坑之后加的。构建脚本默认只出 arm64，而跑测默认用 amd64，
    于是 dist/linux/amd64/open-computer-use 停在 8-1 20:36 整整没动过，
    而那之后有 **8 个改 MCP 本体的提交**——进程名解析、text-attrs 不再乱作证、
    菜单隐藏项、appNotFound 列窗口、include_screenshot、真 diff、Calc 渲染
    NameError——**一个都没进到 cc 实际跑的程序里**。

    更坏的是它不报错，只是让人得出错误的因果：我据此写下"第 51 题从 31 步失败
    变成 6 步通过是菜单修复的直接效果"，而那个修复当时根本不在二进制里，
    真实原因只是两次运行的差异。**一个不报错的陈旧构建，比一个编译失败危险得多。**

    所以这里宁可拦住整轮跑测，也不让它带着旧二进制跑下去。
    """
    if not os.path.exists(binary):
        raise RuntimeError("MCP 二进制不存在：{}。先跑 "
                           "scripts/build-open-computer-use-linux.sh --arch amd64"
                           .format(binary))
    built = os.path.getmtime(binary)
    source_dir = os.path.join(REPO, "apps", "OpenComputerUseLinux")
    newest, newest_at = None, 0.0
    for root, _dirs, files in os.walk(source_dir):
        for name in files:
            if not name.endswith((".go", ".py")):
                continue
            if name.endswith("_test.go") or name.endswith("_test.py"):
                continue
            path = os.path.join(root, name)
            stamp = os.path.getmtime(path)
            if stamp > newest_at:
                newest, newest_at = path, stamp
    if newest is not None and newest_at > built:
        raise RuntimeError(
            "MCP 二进制比源码旧，测的会是上一版。\n"
            "  二进制 {}  ({})\n"
            "  最新源码 {}  ({})\n"
            "先重新构建：scripts/build-open-computer-use-linux.sh --arch amd64".format(
                binary, time.strftime("%m-%d %H:%M", time.localtime(built)),
                os.path.relpath(newest, REPO),
                time.strftime("%m-%d %H:%M", time.localtime(newest_at))))


# claude CLI 的候选位置。
#
# 实测第 190 题当场炸了：`FileNotFoundError: No such file or directory: 'claude'`。
# 它装在 ~/.npm-global/bin/，而那次后台跑测继承到的 PATH 只有
# /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin。
#
# 跑测依赖"调用者的 PATH 恰好对"是个脆弱点：同一台机器、同一份代码，
# 换个终端或换个 cron 环境就跑不起来，而错误发生在 register_mcp 里，
# 看上去像 MCP 出了问题。所以这里自己找一遍。
CLAUDE_CANDIDATES = (
    os.path.expanduser("~/.npm-global/bin"),
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/.claude/local"),
    "/usr/local/bin",
)


def ensure_claude_on_path():
    """把 claude 所在目录补进 PATH。找不到就直接说清楚，别让它在别处炸。"""
    if shutil.which("claude"):
        return
    for directory in CLAUDE_CANDIDATES:
        if os.path.exists(os.path.join(directory, "claude")):
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            return
    raise RuntimeError(
        "PATH 里找不到 claude，也不在这些位置：{}。"
        "跑测靠 `claude -p` 驱动真实的 Claude Code，没有它什么都跑不了。"
        .format(", ".join(CLAUDE_CANDIDATES)))


def register_mcp(binary, workdir):
    ensure_claude_on_path()
    assert_binary_is_fresh(binary)
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
    # "GIMP doesn't have a \"Blue\" theme" —— 第 65 题 cc 的原话。
    #
    # 它做的事完全正确：进到 Edit → Preferences → Theme，核实了列表里只有
    # Dark/Gray/Light/System 四项、没有 Blue，还把四个主题的路径一并列了出来。
    # 这是一次**有证据的**拒绝，比空口说"做不到"强得多。而旧的关键词表把它判成
    # 0.0，理由是"它可能以为自己做成了"——恰恰相反，它比谁都清楚做不成。
    #
    # 仪器的假阴性和模型的失败混在一起，是这套跑测里最该避免的污染：
    # 它会让人去修一个并不存在的模型问题。
    "does not have", "doesn't have", "has no ", "have no ",
    "not offer", "does not offer", "doesn't offer",
    "no option", "no built-in", "no such option",
    "not one of", "isn't one of", "is not one of",
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

    # agent 的工作目录也要能按 worker 分开：`claude mcp add` 把服务器写进
    # **这个目录**的项目级配置，两个 worker 共用一个目录就会互相覆盖，
    # 而且 rmtree 会把对方正在用的配置删掉。
    workdir = os.environ.get("OSWORLD_WORKDIR", "/tmp/ocu-agent-run")
    # **每题都要把 agent 的自动记忆清掉。**
    #
    # 工作目录每题都一样，所以记忆路径也一样
    # （~/.claude/projects/-tmp-ocu-agent-run/memory/）。实测跑到第 152 题时
    # 那里已经攒了 41 个文件，全是 agent 自己总结的操作经验——
    # 也就是说后面的题带着前面题学到的东西在做，题与题不再独立。
    #
    # 比"作弊"更糟的是它**掩盖了缺陷**：那些笔记里写着
    #   「type_text 把 + 变成 =、吞掉换行」
    #   「Position/Size 的 set_value 报成功但没到文档」
    #   「每个 OCU 工具都报 indexer 未定义时改用 pyatspi」
    # ——这些正是我该修的链路问题，而 agent 记住绕路方法之后，
    # 它们再也不会出现在轨迹的报错里。工具的缺陷被 agent 的记忆吸收掉了。
    #
    # 笔记本身很有价值，所以先归档进 docs/osworld/agent-memory/ 再删。
    memory_dir = os.path.join(
        os.path.expanduser("~/.claude/projects"),
        "-" + workdir.strip("/").replace("/", "-"), "memory")
    if os.path.isdir(memory_dir):
        archive = os.path.join(REPO, "docs", "osworld", "agent-memory")
        os.makedirs(archive, exist_ok=True)
        for name in os.listdir(memory_dir):
            if not name.endswith(".md"):
                continue
            src = os.path.join(memory_dir, name)
            dst = os.path.join(archive, name)
            if not os.path.exists(dst):
                shutil.copy(src, dst)
        shutil.rmtree(memory_dir, ignore_errors=True)
    shutil.rmtree(workdir, ignore_errors=True)
    register_mcp(args.binary, workdir)
    # 文件名里**必须带 attempt**。
    #
    # 原来只带任务 id，于是第二次重试会把第一次的轨迹覆盖掉——而失败的那次
    # 恰恰是最该留的：要查"它为什么没做成"，只能看那一次的轨迹。
    # 发现时已经有 31 道题跑过多次，它们早先几次的轨迹都丢了。
    #
    # 每次重试本身是**全新会话**（没有 --resume/--continue，init 事件里
    # session_id 每次都不同），所以这几份轨迹是相互独立的记录，不是续写。
    transcript = "/tmp/osworld-agent-{}-a{}.jsonl".format(task_id[:8], args.attempt)
    trace = "/tmp/osworld-trace-{}.jsonl".format(task_id[:8])
    # 这里**试过**用 CLAUDE_CONFIG_DIR 指向一个空目录来隔离用户级配置，
    # 实测两头落空，已放弃：
    #   1. skills 照样被加载（init 事件里那 14 个一个不少），它们不在这个目录下；
    #   2. MCP 反而没了——`claude mcp add` 写的是默认配置，
    #      而 agent 读的是空目录，ocu 工具数直接变成 0，那一趟跑测全废。
    # 真正起作用的是把 Skill / Workflow 等**工具**禁掉：技能仍然列在 init 里，
    # 但没有任何工具能调用它们。见 DISALLOWED_BASE。
    environ = dict(os.environ, OPEN_COMPUTER_USE_TRACE_FILE=trace)

    # Bash 默认开着，--no-bash 可以退回"纯链路"口径。见 DISALLOWED_BASE。
    disallowed = list(DISALLOWED_BASE)
    if args.no_bash:
        disallowed.append("Bash")
    command = [
        "claude", "-p", task["instruction"],
        # OSWorld 给 agent 的系统提示第一句就是
        #   "You are an agent which follow my instruction and perform desktop
        #    computer tasks as instructed."
        # 我们此前只喂原始指令，**这是仪器的不公平**：实测第 17 题的指令是疑问句
        # （"…What should I do?"），cc 于是把它当问题回答了——给了一份正确的
        # 操作建议，一步也没动手。它没做错，是我们没告诉它该动手。
        #
        # 这里只补 OSWorld 那句框定，**不加任何关于本 MCP 工具的提示**——
        # 那会变成给自己的实现开小灶，测出来的数就不能和别人比了。
        # **逐字照抄 OSWorld 官方系统提示的第一句**，一个字都不多加。
        # 见 OSWorld mm_agents/prompts.py:2。
        #
        # 我一度在后面补了一句"Actually operate the computer…; do not merely
        # explain how it could be done."——那超出了官方措辞，等于给自己的实现
        # 额外加了一句提示。**已收回。** 测出来的数要能和别人比，
        # 唯一的办法是框定与别人逐字相同。
        "--append-system-prompt",
        "You are an agent which follow my instruction and perform desktop "
        "computer tasks as instructed.",
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", str(args.budget),
        "--disallowedTools", *disallowed,
    ]
    started = time.time()
    with open(transcript, "w", encoding="utf-8") as handle:
        subprocess.run(command, cwd=workdir, stdout=handle,
                       stderr=subprocess.DEVNULL, text=True, env=environ)
    elapsed = time.time() - started

    archive_trace(transcript)
    stats = summarize(transcript)
    funcs = task.get("evaluator", {}).get("func")
    funcs = [funcs] if isinstance(funcs, str) else (funcs or [])
    if checks_bash_history(task) and not args.no_bash:
        # 分数照记（判据确实跑了），但要在明细里说清楚为什么它注定拿不到分。
        pass
    if needs_real_credentials(task):
        # 分数记 None 而不是 0：这道题**没有被测过**，不是被测失败了。
        score, detail = None, ("这道题需要真实的第三方凭据（Google Drive / 登录态），"
                               "本机不具备——环境不支持，不计为模型失败")
    elif funcs == ["infeasible"]:
        # 官方的 `infeasible` 判据是空函数——它靠 agent 输出 FAIL 来判分。
        # 我们的 agent 不发 FAIL，它说人话，所以判据落在**它有没有拒绝**上。
        #
        # 这条判据必须能被人复核，所以整段自述会原样存进 results.jsonl：
        # 用关键词判"拒绝"天然不精确，把原文留下，读的人可以自己推翻我。
        score, detail = judge_refusal(stats["final"])
    else:
        score, detail = local.evaluate(task)
    if checks_bash_history(task) and (score or 0) < 1.0:
        detail = (str(detail) + "  ⚠️ 这道题的判据要查 ~/.bash_history，"
                  "而 MCP 的 Bash 工具是非交互式 shell、不写 history——"
                  "即使命令完全正确也拿不到分。属于跑法差异，不是模型失败。")
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
        # 口径必须随每条记录一起存。开了 Bash 的跑测**不再单纯反映这条 MCP
        # 链路的能力**——有些题可以完全绕开桌面用 shell 做完。两种口径的数字
        # 混在一起报，等于把两个不同的实验说成一个。
        "bash": not args.no_bash,
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

TRACES = os.path.join(REPO, "docs", "osworld", "traces")


def archive_trace(path):
    """把这一趟的轨迹**剥掉截图**再 gzip 存进版本库。

    为什么要自动做：轨迹是这套跑测里最值钱的东西——缺陷几乎全是从里面读出来的
    （agent 卡住不会报错，它会换一条路，摩擦只体现为多出来的步数和重复调用）。
    而 /tmp 会被清、磁盘会满（本机一度 98%），手工归档必然漏。

    剥 base64 截图的理由是量出来的：26 条原始轨迹含图 172MB，剥图后 16.7MB，
    gzip 后 2.1MB。分析只需要结构，不需要像素。
    """
    try:
        os.makedirs(TRACES, exist_ok=True)
        out = os.path.join(TRACES, os.path.basename(path) + ".gz")
        with open(path, encoding="utf-8", errors="ignore") as src, \
                gzip.open(out, "wt", encoding="utf-8") as dst:
            for line in src:
                dst.write(re.sub(r'"data"\s*:\s*"[A-Za-z0-9+/=]{200,}"',
                                 '"data":"<stripped>"', line))
    except Exception:
        # 归档失败**不许影响判分**。它是诊断设施，不是判据的一部分。
        pass


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
    p.add_argument("--no-bash", action="store_true",
                   help="禁用 Bash，跑纯 MCP 链路口径")
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
