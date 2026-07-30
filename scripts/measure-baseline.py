#!/usr/bin/env python3
"""跑一组已验证的任务链，产出四元组基线（待办 #26）。

四元组：**成功率 / 平均步数 / 平均 token / a11y 通道使用率**。

这不是 LLM 驱动的 agent，是一台**测量仪器**：任务链是脚本化的、已知可完成的
序列，所以任何一项失败都指向 MCP 侧的回归，而不是模型的发挥。这一点很重要——
用 LLM 跑基线的话，四个数会同时受模型波动和工具质量影响，指标就不可归因了。

判据一律用**外部真值**，不采信工具自己的返回值：
  - 文件系统（重命名、写入的文件内容）
  - 应用配置文件（vlcrc / prefs.js）
  - 保存后的文档内部结构（ODT 的 content.xml）

a11y 通道使用率取自每条 Note 上的 `[semantic]` / `[synthesis]` 标签——
这正是本项目的核心主张（a11y 优先）能否成立的直接度量。

用法:
  scripts/measure-baseline.py                    # 全部任务
  scripts/measure-baseline.py --task vlc-preference
  scripts/measure-baseline.py --list
  scripts/measure-baseline.py --json /tmp/baseline.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(REPO_ROOT, "dist", "linux", "amd64", "open-computer-use")


class MCP:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._id = 0
        threading.Thread(target=self._drain, daemon=True).start()
        # 四元组的原料在这里累积
        self.steps = 0
        self.observation_chars = 0
        self.semantic_notes = 0
        self.synthesis_notes = 0

    def _drain(self):
        for _ in self.process.stderr:
            pass

    def send(self, method, params=None, notify=False, timeout=180):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self._id += 1
            message["id"] = self._id
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        if notify:
            return None
        box = {}
        thread = threading.Thread(
            target=lambda: box.setdefault("line", self.process.stdout.readline()),
            daemon=True)
        thread.start()
        thread.join(timeout)
        if not box.get("line"):
            raise RuntimeError("MCP 无响应: {}".format(method))
        return json.loads(box["line"])

    def handshake(self):
        self.send("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "baseline", "version": "1"}})
        self.send("notifications/initialized", {}, notify=True)

    def call(self, name, arguments):
        """执行一步，并把四元组需要的原料记下来。

        步数只算**动作**，不算观测：agent 每步动作前都要取一次状态，
        把观测也算成步数会让指标失去与"任务需要多少次操作"的对应关系。
        token 则相反，必须把观测算进去——那才是成本的大头。
        """
        response = self.send("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result", {})
        text = ""
        for item in result.get("content", []):
            if item.get("type") == "text":
                text = item.get("text", "")
                break
        self.observation_chars += len(text)
        if name not in ("get_app_state", "get_screenshot", "list_apps"):
            self.steps += 1
            for line in text.splitlines():
                if not line.startswith("Note:"):
                    continue
                if "[semantic]" in line:
                    self.semantic_notes += 1
                elif "[synthesis]" in line:
                    self.synthesis_notes += 1
        return text, bool(result.get("isError"))

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


def find_index(tree, pattern):
    for line in tree.splitlines():
        stripped = line.strip()
        if stripped and stripped[0].isdigit() and re.search(pattern, stripped):
            return stripped.split(" ", 1)[0]
    return None


def window_title(tree):
    lines = tree.splitlines()
    return lines[1] if len(lines) > 1 else ""


def click(client, app, tree, pattern, method="auto"):
    index = find_index(tree, pattern)
    if index is None:
        return False, "找不到 {}".format(pattern)
    _, error = client.call("click", {"app": app, "element_index": index,
                                     "click_method": method})
    return (not error), ("" if not error else "点击失败")


def launch(command, ready, timeout=90):
    subprocess.Popen("setsid {} </dev/null >/dev/null 2>&1 &".format(command),
                     shell=True, start_new_session=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
        if any(ready in line for line in out.splitlines()):
            time.sleep(4)
            return True
        time.sleep(2)
    return False


# --- 任务链。每条给出 setup / run / verify，verify 必须读外部真值 ---

def task_nautilus_rename(client, workdir):
    """在文件管理器里把一个文件改名。判据：文件系统。"""
    target = os.path.join(workdir, "before.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("payload\n")
    subprocess.run(["pkill", "-f", "nautilus"], capture_output=True)
    time.sleep(2)
    if not launch("nautilus {}".format(workdir), os.path.basename(workdir)):
        return False, "Nautilus 起不来"

    app = "org.gnome.Nautilus"
    tree, _ = client.call("get_app_state", {"app": app})
    index = find_index(tree, r"canvas before\.txt")
    if index is None:
        return False, "树里找不到 before.txt"
    client.call("invoke_element_action",
                {"app": app, "element_index": index, "action": "menu"})
    time.sleep(2.5)

    tree, _ = client.call("get_app_state", {"app": app})
    ok, why = click(client, app, tree, r"menu item Rename")
    if not ok:
        return False, why
    time.sleep(2.5)

    tree, _ = client.call("get_app_state", {"app": app})
    entry = find_index(tree, r"text .*Value: before\.txt")
    if entry is None:
        return False, "找不到重命名输入框"
    client.call("press_key", {"app": app, "key": "ctrl+a"})
    client.call("type_text", {"app": app, "element_index": entry, "text": "after.txt"})
    time.sleep(1)

    tree, _ = client.call("get_app_state", {"app": app})
    ok, why = click(client, app, tree, r"push button Rename")
    if not ok:
        return False, why
    time.sleep(2.5)

    renamed = os.path.join(workdir, "after.txt")
    if os.path.exists(renamed) and not os.path.exists(target):
        return True, "文件系统确认 before.txt -> after.txt"
    return False, "文件系统未确认改名"


def task_vlc_preference(client, workdir):
    """改一项 VLC 首选项。判据：~/.config/vlc/vlcrc。"""
    rc = os.path.expanduser("~/.config/vlc/vlcrc")
    subprocess.run(["pkill", "-f", "vlc"], capture_output=True)
    time.sleep(2)
    if not launch("vlc --no-video", "VLC media player"):
        return False, "VLC 起不来"

    app = "vlc"
    tree, _ = client.call("get_app_state", {"app": app})
    ok, why = click(client, app, tree, r"menu item Preferences")
    if not ok:
        return False, why
    time.sleep(3)

    tree, _ = client.call("get_app_state", {"app": app})
    if "Preferences" not in window_title(tree):
        return False, "首选项没打开"
    index = find_index(tree, r"check box Show controls in full screen mode")
    if index is None:
        return False, "找不到目标复选框"
    before = "qt-fs-controller=0" in open(rc, encoding="utf-8", errors="replace").read()
    client.call("click", {"app": app, "element_index": index, "click_method": "auto"})
    time.sleep(1.5)

    tree, _ = client.call("get_app_state", {"app": app})
    ok, why = click(client, app, tree, r"push button Save")
    if not ok:
        return False, why
    time.sleep(3)

    after = "qt-fs-controller=0" in open(rc, encoding="utf-8", errors="replace").read()
    if after != before:
        return True, "vlcrc 中 qt-fs-controller 已变化"
    return False, "vlcrc 未发生预期变化"


def task_gedit_type(client, workdir):
    """在文本编辑器里写入并保存。判据：磁盘文件内容。"""
    target = os.path.join(workdir, "note.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("old\n")
    subprocess.run(["pkill", "-f", "gedit"], capture_output=True)
    time.sleep(2)
    if not launch("gedit {}".format(target), "note.txt"):
        return False, "gedit 起不来"

    app = "gedit"
    client.call("get_app_state", {"app": app})
    client.call("press_key", {"app": app, "key": "ctrl+a"})
    time.sleep(0.6)
    client.call("type_text", {"app": app, "text": "baseline-marker"})
    time.sleep(1)
    client.call("press_key", {"app": app, "key": "ctrl+s"})
    time.sleep(2.5)

    content = open(target, encoding="utf-8", errors="replace").read()
    if "baseline-marker" in content:
        return True, "磁盘文件包含写入内容"
    return False, "磁盘文件内容为 {!r}".format(content[:40])


def task_thunderbird_folder(client, workdir):
    """在邮件客户端里切换文件夹。判据：树里的 `[selected focused]` 转移。

    加这条是为了让基线覆盖**第三种工具包**（Gecko/XUL）——原先只有 GTK 与 Qt。
    不同工具包的语义执行可靠性差别很大（实测 Qt > Gecko ≈ GAIL > GTK > Electron），
    基线只盖两种的话，a11y 通道占比这个数会被工具包构成带偏。

    判据用**选中态的转移**而不是"点击返回成功"：今天在多个应用上确认过，
    动作返回成功不等于生效。
    """
    subprocess.run(["pkill", "-f", "thunderbird"], capture_output=True)
    time.sleep(3)
    if not launch("thunderbird", "Mozilla Thunderbird"):
        return False, "Thunderbird 起不来"

    app = "Thunderbird"
    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 2500})
    before = find_index(tree, r"tree item Trash")
    inbox = find_index(tree, r"tree item Inbox")
    target, name = (inbox, "Inbox") if inbox else (before, "Trash")
    if target is None:
        return False, "文件夹树里既没有 Inbox 也没有 Trash"

    _, error = client.call("click", {"app": app, "element_index": target,
                                     "click_method": "auto"})
    if error:
        return False, "点击文件夹失败"
    time.sleep(2.5)

    tree, _ = client.call("get_app_state", {"app": app, "max_tree_nodes": 2500})
    for line in tree.splitlines():
        stripped = line.strip()
        if "tree item {}".format(name) in stripped and "[selected" in stripped:
            return True, "{} 已取得 [selected]".format(name)
    return False, "{} 没有取得选中态".format(name)


TASKS = {
    "thunderbird-folder": (task_thunderbird_folder, "邮件客户端切换文件夹（Gecko/XUL）"),
    "nautilus-rename": (task_nautilus_rename, "文件管理器重命名（GTK）"),
    "vlc-preference": (task_vlc_preference, "VLC 首选项改动（Qt）"),
    "gedit-type": (task_gedit_type, "文本编辑器写入并保存（GTK）"),
}


def run_one(name, binary):
    fn, _ = TASKS[name]
    workdir = tempfile.mkdtemp(prefix="ocu-baseline-")
    client = MCP(binary)
    started = time.time()
    try:
        client.handshake()
        ok, detail = fn(client, workdir)
    except Exception as error:
        ok, detail = False, "异常：{}".format(error)
    finally:
        client.close()
        shutil.rmtree(workdir, ignore_errors=True)
    total_notes = client.semantic_notes + client.synthesis_notes
    return {
        "task": name,
        "ok": ok,
        "detail": detail,
        "steps": client.steps,
        # 4 字符 ≈ 1 token 是本仓库其它脚本一贯的粗估口径，保持一致以便横向比
        "tokens": client.observation_chars // 4,
        "semantic": client.semantic_notes,
        "synthesis": client.synthesis_notes,
        "a11y_rate": (100 * client.semantic_notes // total_notes) if total_notes else None,
        "seconds": round(time.time() - started, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="产出四元组基线")
    parser.add_argument("--binary", default=DEFAULT_BIN)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", help="把结果另存为 JSON")
    args = parser.parse_args()

    if args.list:
        print("可用任务：")
        for name, (_, desc) in TASKS.items():
            print("  {:20} {}".format(name, desc))
        return 0
    if not os.path.exists(args.binary):
        print("找不到可执行文件 {}".format(args.binary), file=sys.stderr)
        return 2

    names = args.task or list(TASKS)
    for name in names:
        if name not in TASKS:
            print("未知任务 {!r}，用 --list 查看".format(name), file=sys.stderr)
            return 2

    rows = []
    for name in names:
        print("跑 {} …".format(name), flush=True)
        row = run_one(name, args.binary)
        rows.append(row)
        print("  {}  步数={} token={} a11y={}  {}".format(
            "PASS" if row["ok"] else "FAIL", row["steps"], row["tokens"],
            "n/a" if row["a11y_rate"] is None else "{}%".format(row["a11y_rate"]),
            row["detail"][:52]))

    done = [r for r in rows if r["ok"]]
    notes = sum(r["semantic"] + r["synthesis"] for r in rows)
    semantic = sum(r["semantic"] for r in rows)
    print()
    print("=" * 60)
    print("四元组基线（{} 个任务）".format(len(rows)))
    print("  成功率        {}/{} = {}%".format(
        len(done), len(rows), 100 * len(done) // max(len(rows), 1)))
    print("  平均步数      {:.1f}".format(
        sum(r["steps"] for r in rows) / max(len(rows), 1)))
    print("  平均 token    {:.0f}".format(
        sum(r["tokens"] for r in rows) / max(len(rows), 1)))
    print("  a11y 通道占比 {}".format(
        "n/a" if not notes else "{}% （{}/{} 条动作 Note）".format(
            100 * semantic // notes, semantic, notes)))
    print("=" * 60)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"tasks": rows}, handle, ensure_ascii=False, indent=2)
        print("已写入 {}".format(args.json))
    return 0 if len(done) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
