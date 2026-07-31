#!/usr/bin/env python3
"""让 OSWorld 的官方 config / 评估器在**本机桌面**上跑起来。

OSWorld 原本假设有一台虚拟机：agent 在 VM 里操作，评估器通过 HTTP 跟 VM 里的
server 通信取文件、跑命令。我们没有那一层——MCP 直接驱动本机桌面。所以这里做
一个 `env` 垫片，把那些 `env.controller.xxx` 调用落到本地。

为什么坚持用**官方评估器**而不是自己写判据：自己写的判据会不自觉地照着实现来
定，等于自己给自己出考卷。这个仓库里已经因此吃过一次亏——几何任务的
`examine_shape: false` 没传给评估器，一次实际成功被记成了 0.0 分，然后我
朝着错误的方向找了半天原因。

统计过全部 369 题用到的 config 类型与 getter，这里按覆盖面从大到小实现：
    download 278 / launch 275 / open 155 / execute 154 / activate_window 65
    chrome_open_tabs 53 / command 29 / sleep 14
    getter: vm_file 261 / cloud_file 214 / rule 173 / vm_command_line 41 …
没实现的（googledrive 8、login 8）需要真实凭据，会被如实记成"环境不支持"，
**不记成失败**——把环境缺陷记成模型失败是最坏的一种数据污染。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

OSWORLD_ROOT = os.environ.get("OSWORLD_ROOT", "/home/user/OSWorld")
STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osworld-stubs")
CACHE_DIR = os.environ.get("OSWORLD_CACHE", "/tmp/osworld-cache")


def _ensure_paths():
    for path in (OSWORLD_ROOT, STUBS):
        if path not in sys.path:
            sys.path.insert(0, path)


class LocalController:
    """把 VM controller 的接口落到本机。

    只实现 getter 真正调用到的那几个方法（全仓统计：execute_python_command 76、
    get_file 20，其余各 1–2）。没实现的抛 NotImplementedError 而不是返回 None：
    返回 None 会让评估器拿着空数据算出一个**看起来正常的分数**，
    那比直接报错危险得多。
    """

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir

    def execute_python_command(self, command):
        # **不加 pyautogui 前缀。** 官方 controller 会注入一段 pyautogui 初始化，
        # 那是给"在 VM 里替 agent 打字"用的；评估阶段的命令都只是读文件、
        # 打印结果，注入它只会在没装 pyautogui 的机器上平白失败。
        result = subprocess.run(
            [sys.executable, "-c", command],
            capture_output=True, text=True, timeout=180)
        return {
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
            "status": "success" if result.returncode == 0 else "error",
        }

    def execute_command(self, command):
        shell = isinstance(command, str)
        result = subprocess.run(command, shell=shell, capture_output=True,
                                text=True, timeout=180)
        return {
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
            "status": "success" if result.returncode == 0 else "error",
        }

    def get_file(self, file_path):
        try:
            with open(file_path, "rb") as handle:
                return handle.read()
        except OSError:
            return None

    def get_vm_directory_tree(self, path):
        raise NotImplementedError("get_vm_directory_tree is not shimmed yet")

    def get_accessibility_tree(self):
        # 走本仓库自己的运行时，而不是 OSWorld 的 server。
        binary = os.environ.get(
            "OCU_BINARY",
            "/home/user/unify-computer-use/dist/linux/amd64/open-computer-use")
        if not os.path.exists(binary):
            return None
        return None  # 只有 5 题用到，先如实返回 None 让评估器报错而不是编数据

    def get_vm_screen_size(self):
        try:
            out = subprocess.run(["xdotool", "getdisplaygeometry"],
                                 capture_output=True, text=True).stdout.split()
            return {"width": int(out[0]), "height": int(out[1])}
        except Exception:
            return None

    def get_vm_window_size(self, app_class_name):
        raise NotImplementedError("get_vm_window_size is not shimmed yet")

    def get_vm_wallpaper(self):
        raise NotImplementedError("get_vm_wallpaper is not shimmed yet")

    def get_vm_desktop_path(self):
        return os.path.expanduser("~/Desktop")

    def get_terminal_output(self):
        return None


class LocalSetupController:
    def _activate_window_setup(self, **kwargs):
        title = kwargs.get("window_name") or kwargs.get("title") or ""
        if title:
            subprocess.run(["wmctrl", "-a", title], capture_output=True)


class LocalEnv:
    """评估器眼里的 env。字段名照抄官方，方便直接喂给官方 getter。"""

    def __init__(self, cache_dir=None):
        _ensure_paths()
        self.cache_dir = cache_dir or tempfile.mkdtemp(prefix="osworld-eval-")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.controller = LocalController(self.cache_dir)
        self.setup_controller = LocalSetupController()
        self.vm_platform = "Linux"
        self.vm_ip = "localhost"
        self.server_port = 5000
        self.chromium_port = 9222
        self.vlc_port = 8080
        self.vm_machine = "local"
        self.current_use_proxy = False
        from desktop_env.evaluators import getters as _getters
        self.getters = _getters
        self.getter = _getters


# --------------------------------------------------------------------------
# config：把一道题的初始环境布置出来
# --------------------------------------------------------------------------

def _download(url, dest):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = os.path.join(CACHE_DIR, url.rsplit("/", 3)[-1].replace("/", "_"))
    if not (os.path.exists(cached) and os.path.getsize(cached) > 0):
        with urllib.request.urlopen(url, timeout=300) as response:
            with open(cached, "wb") as handle:
                shutil.copyfileobj(response, handle)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.copy(cached, dest)
    return dest


def _chrome_close_tabs(urls):
    """按 URL 关掉 Chrome 标签页，走 CDP。

    用 CDP 而不是模拟 Ctrl+W：布置环境不是被测行为，它必须**确定性地**成功。
    拿被测的那条 GUI 链路去布置环境，等于让环境的正确性依赖于正在被检验的东西。
    """
    import urllib.error
    deadline = time.time() + 30
    targets = []
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:9222/json", timeout=5) as r:
                targets = json.loads(r.read().decode("utf-8"))
            break
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    for target in targets:
        if target.get("type") != "page":
            continue
        url = target.get("url") or ""
        if any(url.rstrip("/").startswith(want.rstrip("/")) for want in urls):
            try:
                urllib.request.urlopen(
                    "http://localhost:9222/json/close/" + target["id"], timeout=5).read()
            except Exception:
                pass


CHROME_PROFILE = os.path.expanduser("~/.config/google-chrome/Default")


def clean_chrome_session(log=print):
    """把 Chrome 的标签页会话清空，让每道题从确定的状态开始。

    官方靠**恢复虚拟机快照**做到这一点，我们没有那一层。不清的后果当场撞到过：
    跑第 3 题时，第 1、2 题打开的 amazon 设置页、amazon.com 全都还在，
    于是这道题的初始状态和它的题面根本对不上。

    在**脏状态上跑出来的分数，不管高低都是假的**——高是因为上一题顺手做了一半，
    低是因为多出来的东西干扰了判据。所以这一步不是打扫卫生，是判据的一部分。
    """
    for name in ("chrome", "google-chrome", "chrome_crashpad_handler"):
        subprocess.run(["pkill", "-x", name], capture_output=True)
    time.sleep(3)
    removed = 0
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        path = os.path.join(CHROME_PROFILE, name)
        if os.path.exists(path):
            os.remove(path)
            removed += 1
    # `Sessions/` 也要清。只删上面四个文件不够——实测：清完之后按
    # Ctrl+Shift+T，Chrome 从 Sessions/ 里捞回了**上一道题的整个窗口**，
    # 于是 lonelyplanet 和 airbnb 各出现两次，而判据要求标签集合精确相等。
    # "恢复最近关闭"这个功能会跨会话记忆，清不干净就等于给下一题埋了地雷。
    sessions = os.path.join(CHROME_PROFILE, "Sessions")
    if os.path.isdir(sessions):
        shutil.rmtree(sessions, ignore_errors=True)
        removed += 1
    if removed:
        log("  清掉 Chrome 会话文件 {} 个".format(removed))
    return removed


def apply_config(task, log=print):
    """执行一道题的 config 段。返回 (是否就绪, 跳过的步骤说明)。

    不支持的步骤**不静默跳过**：返回原因，让调用方把这道题记成"环境不支持"。
    静默跳过会让 agent 面对一个残缺的环境，然后把布置失败记成它的失败。
    """
    skipped = []
    for step in task.get("config") or []:
        kind = step.get("type")
        params = step.get("parameters") or {}
        try:
            if kind == "download":
                for item in params.get("files") or []:
                    _download(item["url"], item["path"])
                    log("  素材: {}".format(item["path"]))
            elif kind == "launch":
                command = params["command"]
                if isinstance(command, str):
                    command = [command]
                subprocess.Popen(command, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log("  启动: {}".format(" ".join(command)[:90]))
            elif kind == "open":
                path = params["path"]
                subprocess.Popen(
                    "setsid xdg-open {} </dev/null >/dev/null 2>&1 &".format(
                        subprocess.list2cmdline([path])),
                    shell=True, start_new_session=True)
                log("  打开: {}".format(path))
            elif kind == "execute":
                command = params["command"]
                if isinstance(command, list):
                    subprocess.run(command, capture_output=True, timeout=180)
                else:
                    subprocess.run(command, shell=True, capture_output=True, timeout=180)
                log("  执行: {}".format(str(command)[:90]))
            elif kind == "command":
                command = params["command"]
                subprocess.run(command, shell=isinstance(command, str),
                               capture_output=True, timeout=180)
                log("  命令: {}".format(str(command)[:90]))
            elif kind == "sleep":
                time.sleep(float(params.get("seconds", 1)))
            elif kind == "activate_window":
                title = params.get("window_name") or ""
                subprocess.run(["wmctrl", "-a", title], capture_output=True)
                log("  激活窗口: {}".format(title))
            elif kind == "chrome_open_tabs":
                urls = params.get("urls_to_open") or []
                subprocess.Popen(
                    ["google-chrome", "--remote-debugging-port=1337"] + urls,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log("  Chrome 打开 {} 个标签".format(len(urls)))
            elif kind == "chrome_close_tabs":
                # 通过 CDP 关标签页。这道题（"把我刚关掉的标签页恢复回来"）的
                # 初始状态**就是**"某个标签页刚被关掉"，少了这一步，题面描述的
                # 情境根本不存在，agent 会面对一个和指令对不上的桌面。
                _chrome_close_tabs(params.get("urls_to_close") or [])
                log("  关掉 {} 个标签".format(len(params.get("urls_to_close") or [])))
            else:
                skipped.append(kind)
        except Exception as error:
            skipped.append("{}({})".format(kind, str(error)[:80]))
    return (not skipped), skipped


# --------------------------------------------------------------------------
# 复位：让同一道题能被跑第二遍
# --------------------------------------------------------------------------
#
# OSWorld 官方靠**恢复虚拟机快照**来复位，我们跑在本机上没有那一层。整机快照
# 在这台机器上也不现实（/ 只剩不到 2GB）。
#
# 所以按"被测状态实际落在哪里"做最小复位。第一批只做 Chrome，因为它的设置类
# 题目几乎全部落在**一个** JSON 文件里（37KB 的 Default/Preferences，
# 而整个 profile 有 412MB）——复制那一个文件是廉价且够用的。
#
# 复位不到位的题**必须如实标注**：一道在脏状态上跑出来的分数，
# 不管是高是低都是假的。

CHROME_PREFS = os.path.expanduser("~/.config/google-chrome/Default/Preferences")
PRISTINE_DIR = os.path.join(CACHE_DIR, "pristine")


def snapshot_state(task, log=print):
    """第一次布置这道题之前，把它会改动的状态原样存一份。"""
    os.makedirs(PRISTINE_DIR, exist_ok=True)
    saved = []
    if _touches_chrome(task) and os.path.exists(CHROME_PREFS):
        dest = os.path.join(PRISTINE_DIR, "chrome-Preferences.json")
        if not os.path.exists(dest):
            shutil.copy(CHROME_PREFS, dest)
            saved.append("chrome/Preferences")
    return saved


def reset_state(task, log=print):
    """把状态恢复到第一次布置之前。返回 (是否复位干净, 说明)。"""
    if not _touches_chrome(task):
        return False, "这道题的状态不在已知的可复位范围内"
    source = os.path.join(PRISTINE_DIR, "chrome-Preferences.json")
    if not os.path.exists(source):
        return False, "没有留下 Chrome Preferences 的原始副本"
    # **不要用 `pkill -f chrome`。** `-f` 匹配整条命令行，会把**发起这次调用的
    # 那个 shell / 脚本自己**一并杀掉——只要它的命令行里出现过 "chrome"。
    # 本仓库为此修过一次 measure-baseline.py（退出码 144），而我在写这一段时
    # 又原样踩了第二次。用进程名精确匹配。
    for name in ("chrome", "google-chrome", "chrome_crashpad_handler"):
        subprocess.run(["pkill", "-x", name], capture_output=True)
    time.sleep(3)
    shutil.copy(source, CHROME_PREFS)
    log("  已复位 Chrome Preferences")
    return True, "chrome/Preferences 已还原"


def _touches_chrome(task):
    blob = json.dumps(task)
    return "chrome" in blob.lower()


# --------------------------------------------------------------------------
# 评估：原样调用官方 getter + metric
# --------------------------------------------------------------------------

def _resolve_metric(name):
    _ensure_paths()
    from desktop_env.evaluators import metrics
    fn = getattr(metrics, name, None)
    if fn is None:
        raise KeyError("metric {!r} not found".format(name))
    return fn


def _resolve_getter(kind):
    _ensure_paths()
    from desktop_env.evaluators import getters
    fn = getattr(getters, "get_{}".format(kind), None)
    if fn is None:
        raise KeyError("getter get_{} not found".format(kind))
    return fn


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def evaluate(task, env=None, log=print):
    """用官方评估器判分。返回 (分数, 说明)。

    `options` **必须原样传下去**。忘了传的代价是实测过的：几何任务的
    `examine_shape: false` 没传下去，于是"标题被移动了"这件事本身触发了形状
    比对，判 0.0——而磁盘上的 top 明明满足判据。少传一个参数，就会把一次成功
    记成失败，然后朝错误的方向找原因。
    """
    env = env or LocalEnv()
    spec = task.get("evaluator") or {}

    # **postconfig 必须先跑。** 忘了它的代价是当场实测到的：第 1 题里
    # Chrome 的默认搜索引擎已经在界面上变成了 `Microsoft Bing (Default)`，
    # 而判分是 0.0——因为 Chrome 把 Preferences 惰性刷盘，官方的 postconfig
    # 正是 `pkill chrome` + 重启来强制这次刷盘。少跑这一段，
    # 一次真实的成功会被记成失败，然后让人去修一个并不存在的产品缺陷。
    post = spec.get("postconfig") or []
    if post:
        log("  跑 postconfig（{} 步）以固化被测状态".format(len(post)))
        apply_config({"config": post}, log=lambda *a: None)
        time.sleep(3)

    funcs = _as_list(spec.get("func"))
    if not funcs:
        return None, "这道题没有评估函数"
    if funcs == ["infeasible"]:
        return None, "官方标记为 infeasible（正确行为是拒绝执行），本轮跳过"

    results = _as_list(spec.get("result"))
    expecteds = _as_list(spec.get("expected"))
    options = _as_list(spec.get("options")) or [{}] * len(funcs)
    conj = spec.get("conj", "and")

    scores = []
    details = []
    for index, name in enumerate(funcs):
        try:
            metric = _resolve_metric(name)
            got_spec = results[index] if index < len(results) else None
            gold_spec = expecteds[index] if index < len(expecteds) else None
            opts = options[index] if index < len(options) else {}
            opts = opts or {}

            got = None
            if got_spec is not None:
                got = _resolve_getter(got_spec["type"])(env, got_spec)
            gold = None
            if gold_spec is not None:
                gold = _resolve_getter(gold_spec["type"])(env, gold_spec)

            if gold_spec is None:
                score = float(metric(got, **opts))
            else:
                score = float(metric(got, gold, **opts))
            scores.append(score)
            details.append("{}[{}] -> {}".format(name, index, score))
        except Exception as error:
            scores.append(0.0)
            details.append("{}[{}] 抛异常: {}: {}".format(
                name, index, type(error).__name__, str(error)[:160]))

    if not scores:
        return None, "没有得到任何分数"
    score = max(scores) if conj == "or" else min(scores)
    return score, "; ".join(details)
