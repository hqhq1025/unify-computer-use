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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

OSWORLD_ROOT = os.environ.get("OSWORLD_ROOT", "/home/user/OSWorld")
STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osworld-stubs")
CACHE_DIR = os.environ.get("OSWORLD_CACHE", "/tmp/osworld-cache")

# Chrome 的远程调试端口，**只有这一个来源**。
#
# 原来这里是两个数：官方任务 config 用 `--remote-debugging-port=1337` 起 Chrome，
# 而垫片里的 CDP 客户端写死连 9222，中间靠一个手工起的
# `socat tcp-listen:9222,fork tcp:localhost:1337` 桥着。那条 socat 不在任何脚本里，
# 换台机器复现时没人会知道要起它——而缺了它不会报错，只是 chrome_open_tabs /
# chrome_close_tabs / 取当前标签页**全部静默失效**，题目环境布置不全，
# 失败会被记到模型头上。隐藏依赖比多一行配置危险得多。
CHROME_CDP_PORT = int(os.environ.get("CHROME_CDP_PORT", "1337"))
CDP = "http://localhost:{}".format(CHROME_CDP_PORT)


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
        """目录树，形状照抄官方 server 的 /list_directory。

        实测第 5 题（用 Chrome 的"创建快捷方式"在桌面上放一个图标）就卡在这里：
        快捷方式**确确实实创建好了**（桌面上有 chrome-Play_Puzzle_Game_2048.desktop），
        判分却因为这个方法没实现而抛异常记 0.0。又一次"仪器给假阴性"。
        """
        if not os.path.isdir(path):
            return None

        def node(current):
            entry = {"name": os.path.basename(current) or current,
                     "type": "directory" if os.path.isdir(current) else "file"}
            if entry["type"] == "directory":
                children = []
                try:
                    for name in sorted(os.listdir(current)):
                        children.append(node(os.path.join(current, name)))
                except OSError:
                    pass
                entry["children"] = children
            return entry

        return node(path)

    def get_accessibility_tree(self):
        """整个桌面的 AT-SPI 树，序列化成 OSWorld server 的那套 XML。

        为什么必须做：`active_url_from_accessTree` 这个 getter 有 **14 道题**在用
        （另有 accessibility_tree 5 题）。它从这份 XML 里用 CSS 选择器捞
        `application[name="Google Chrome"] entry[name="Address and search bar"]`
        的文本当作当前 URL。垫片返回 None 时评估器直接放弃——实测第 13 题
        （跳到密码管理页）地址栏明明是 chrome://password-manager/passwords，
        判分却是 0.0。又一次"仪器缺一块就把成功记成失败"。

        命名空间与属性名**照抄官方 server 的 _create_atspi_node**：
        状态是 `{st}<state>`="true"，几何是 `{cp}screencoord` / `{cp}size`，
        节点文本取 Text 接口并去掉 uFFFC/uFFFD 两个替换符（官方也这么做）。
        照抄不是偷懒，是因为选择器写在官方 getter 里，格式差一个字都选不中。
        """
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            from lxml import etree
        except Exception:
            return None

        ns = {
            "st": "uri:deskat:state.at-spi.gnome.org",
            "attr": "uri:deskat:attributes.at-spi.gnome.org",
            "cp": "uri:deskat:component.at-spi.gnome.org",
            "txt": "uri:deskat:text.at-spi.gnome.org",
            "val": "uri:deskat:value.at-spi.gnome.org",
            "act": "uri:deskat:action.at-spi.gnome.org",
        }

        def safe_call(fn, default=None):
            try:
                return fn()
            except Exception:
                return default

        # 节点预算是硬的：整个桌面的树在这台机器上能到上万节点，
        # 不封顶会让判分本身变成一次超时。
        #
        # **但 8000 太小，会把判据要找的东西截掉。** 实测第 125 题
        # （"把左侧幻灯片面板恢复出来"）：官方 check_left_panel 遍历
        # `document-frame` 找 name="Slides View"，而预算 8000 时整棵树里
        # document-frame **一个都没有**（树到 676KB 就被截断），提到 40000
        # 才出现。判据于是永远返回 0——又一次"仪器缺一块就把成功记成失败"，
        # 而且这次影响 19 道题（accessibility_tree 5 + active_url_from_accessTree 14）。
        #
        # 截断本身是静默的：XML 依然是合法的，只是少了一半，
        # 没有任何迹象提示"你要找的那个节点在被砍掉的那一半里"。
        budget = [int(os.environ.get("OSWORLD_TREE_BUDGET", "60000"))]

        def build(node, depth):
            if budget[0] <= 0 or depth > 40:
                return None
            budget[0] -= 1
            role = safe_call(lambda: node.get_role_name(), "unknown") or "unknown"
            tag = role.replace(" ", "-").replace("/", "-") or "unknown"
            element = etree.Element(tag, nsmap=ns)
            element.set("name", str(safe_call(lambda: node.get_name(), "") or ""))
            state_set = safe_call(lambda: node.get_state_set())
            if state_set is not None:
                for name in ("VISIBLE", "SHOWING", "FOCUSED", "FOCUSABLE", "ENABLED",
                             "SELECTED", "CHECKED", "EXPANDED", "ACTIVE", "MODAL",
                             "EDITABLE", "SENSITIVE"):
                    state = getattr(Atspi.StateType, name, None)
                    if state is not None and safe_call(
                            lambda: state_set.contains(state), False):
                        element.set("{{{}}}{}".format(ns["st"], name.lower()), "true")
            text = ""
            iface = safe_call(lambda: node.get_text_iface())
            if iface is not None:
                count = safe_call(lambda: Atspi.Text.get_character_count(iface), 0) or 0
                if count:
                    text = safe_call(
                        lambda: Atspi.Text.get_text(iface, 0, count), "") or ""
                    text = text.replace("\ufffc", "").replace("\ufffd", "")
            if text:
                element.text = text
            for index in range(min(safe_call(lambda: node.get_child_count(), 0) or 0, 200)):
                child = safe_call(lambda: node.get_child_at_index(index))
                if child is None:
                    continue
                built = build(child, depth + 1)
                if built is not None:
                    element.append(built)
                if budget[0] <= 0:
                    break
            return element

        desktop = safe_call(lambda: Atspi.get_desktop(0))
        if desktop is None:
            return None
        root = etree.Element("desktop-frame", nsmap=ns)
        for index in range(safe_call(lambda: desktop.get_child_count(), 0) or 0):
            app = safe_call(lambda: desktop.get_child_at_index(index))
            if app is None:
                continue
            built = build(app, 0)
            if built is not None:
                root.append(built)
        return etree.tostring(root, encoding="unicode")

    def get_vm_screen_size(self):
        try:
            out = subprocess.run(["xdotool", "getdisplaygeometry"],
                                 capture_output=True, text=True).stdout.split()
            return {"width": int(out[0]), "height": int(out[1])}
        except Exception:
            return None

    def get_vm_window_size(self, app_class_name):
        """按窗口类名取窗口尺寸。"""
        try:
            out = subprocess.run(
                ["xdotool", "search", "--class", app_class_name],
                capture_output=True, text=True).stdout.split()
            if not out:
                return None
            geo = subprocess.run(["xdotool", "getwindowgeometry", out[0]],
                                 capture_output=True, text=True).stdout
            match = re.search(r"Geometry:\s*(\d+)x(\d+)", geo)
            if not match:
                return None
            return {"width": int(match.group(1)), "height": int(match.group(2))}
        except Exception:
            return None

    def get_vm_wallpaper(self):
        """当前壁纸的字节内容。

        判据（compare_images）拿它和标准图比。原来这里直接抛
        NotImplementedError，于是第 273 题判 0——**而那不是模型的失败，
        是仪器缺了一块**。这已经是同一类问题的第 N 次：
        垫片缺一个 getter，判据就整条崩掉，看上去像模型没做成。

        GNOME 的壁纸路径在 gsettings 里，可能是 file:// URI。
        """
        try:
            for key in ("picture-uri-dark", "picture-uri"):
                out = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.background", key],
                    capture_output=True, text=True).stdout.strip().strip("'\"")
                if not out:
                    continue
                path = out
                if path.startswith("file://"):
                    path = urllib.parse.unquote(path[len("file://"):])
                if os.path.exists(path):
                    with open(path, "rb") as handle:
                        return handle.read()
        except Exception:
            pass
        return None

    def get_vm_desktop_path(self):
        return os.path.expanduser("~/Desktop")

    def get_terminal_output(self):
        return None


class LocalSetupController:
    """判据里的 postconfig 会回头调 setup controller 的方法。

    这些方法**官方是按位置传一个 params dict 的**，不是 kwargs。我最初照
    kwargs 写，于是第 196 题当场炸：

        TypeError: _activate_window_setup() takes 1 positional argument
                   but 2 were given

    而那道题的第一个子判据已经是 1.0——cc 很可能做对了，却因为判据自己崩了
    而记成 0.0。所以这里两种调用形式都要吃。
    """

    @staticmethod
    def _params(args, kwargs):
        if args and isinstance(args[0], dict):
            merged = dict(args[0])
            merged.update(kwargs)
            return merged
        return kwargs

    def _activate_window_setup(self, *args, **kwargs):
        params = self._params(args, kwargs)
        title = params.get("window_name") or params.get("title") or ""
        if title:
            subprocess.run(["wmctrl", "-a", title], capture_output=True)
            time.sleep(0.5)

    def _execute_setup(self, *args, **kwargs):
        params = self._params(args, kwargs)
        command = params.get("command")
        if not command:
            return
        subprocess.run(command, shell=isinstance(command, str),
                       capture_output=True, timeout=180)

    def _sleep_setup(self, *args, **kwargs):
        params = self._params(args, kwargs)
        time.sleep(float(params.get("seconds", 1)))

    def __getattr__(self, name):
        # 官方 setup controller 的方法很多，缺一个就让判据整条崩掉，
        # 而那通常和被测的东西无关。没实现的一律当作空操作，
        # 但**不要静默**——打一行出来，免得"少做了一步"被当成正常。
        if not name.endswith("_setup"):
            raise AttributeError(name)

        def missing(*args, **kwargs):
            print("  ⚠️ setup controller 没实现 {}，已跳过".format(name))

        return missing


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
        self.chromium_port = CHROME_CDP_PORT
        self.vlc_port = 8080
        self.vm_machine = "local"
        self.current_use_proxy = False
        from desktop_env.evaluators import getters as _getters
        self.getters = _getters
        self.getter = _getters


# --------------------------------------------------------------------------
# config：把一道题的初始环境布置出来
# --------------------------------------------------------------------------

def _substitute(command):
    """把 config 里的 {CLIENT_PASSWORD} 占位符换掉。

    官方在 setup.py:465 做同样的替换。不替换的后果是命令原样带着大括号跑，
    `sudo -S` 拿到的密码是字面量 "{CLIENT_PASSWORD}"，安装步骤静默失败——
    而失败的是**环境布置**，账却会记到 agent 头上。

    密码从 OSWORLD_CLIENT_PASSWORD 取，没设就用官方镜像的默认值。
    """
    password = os.environ.get("OSWORLD_CLIENT_PASSWORD", "password")
    if isinstance(command, str):
        return command.replace("{CLIENT_PASSWORD}", password)
    if isinstance(command, list):
        return [str(x).replace("{CLIENT_PASSWORD}", password) for x in command]
    return command


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


def _chrome_activate_tab(url):
    """把某个 URL 的标签切到前台，走 CDP。"""
    import urllib.error
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(CDP + "/json", timeout=5) as r:
                targets = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError):
            time.sleep(1)
            continue
        for target in targets:
            if target.get("type") != "page":
                continue
            got = target.get("url") or ""
            if _same_page(got, url):
                try:
                    urllib.request.urlopen(
                        CDP + "/json/activate/" + target["id"],
                        timeout=5).read()
                    return True
                except Exception:
                    return False
        time.sleep(1)
    return False


def _chrome_inject_js(js):
    """在当前活动标签页里执行一段 JS，走 CDP 的 Runtime.evaluate。

    用 websocket 而不是 HTTP：DevTools 的 /json 端点只能列举和开关标签，
    执行代码必须走每个 target 的 webSocketDebuggerUrl。
    """
    if not js:
        return False
    try:
        import websocket  # noqa: F401
    except ImportError:
        return False
    import urllib.error
    try:
        with urllib.request.urlopen(CDP + "/json", timeout=5) as r:
            targets = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError):
        return False
    pages = [t for t in targets if t.get("type") == "page"
             and t.get("webSocketDebuggerUrl")]
    if not pages:
        return False
    import websocket as ws
    for target in pages[:1]:
        try:
            conn = ws.create_connection(target["webSocketDebuggerUrl"], timeout=10)
            conn.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": js}}))
            conn.recv()
            conn.close()
            return True
        except Exception:
            return False
    return False


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
            with urllib.request.urlopen(CDP + "/json", timeout=5) as r:
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
                    CDP + "/json/close/" + target["id"], timeout=5).read()
            except Exception:
                pass


CHROME_PROFILE = os.path.expanduser("~/.config/google-chrome/Default")


# 每题开始前要关掉的残留 GUI 应用。
#
# 官方靠恢复虚拟机快照做到这一点，我们没有那一层。不关的后果是**静默降级**：
# 跑到第 152 题时桌面上堆着 VLC、gedit、图片查看器、Firefox、两个 Chrome 窗口、
# GIMP、Impress——3900MB 内存只剩 151MB 可用，机器开始颠簸，
# `claude -p` 直接挂住不返回，连着两次跑满 timeout 却一行输出都没有。
#
# 不报错，只是越跑越慢、越跑越容易超时，而这些超时看上去像"模型做不完"。
#
# Chrome / GIMP / LibreOffice 不在这里：它们各有专门的清理函数，
# 因为直接 pkill 会留下崩溃恢复对话框。
STALE_APPS = (
    "vlc", "gedit", "eog", "evince", "firefox", "thunderbird",
    "file-roller", "nautilus", "totem", "gnome-calculator",
)


# GNOME 桌面图标扩展（ding）内存泄漏的阈值，超过就重启它。
#
# 实测：这个扩展从 7-29 一直跑到第 152 题，涨到 **1021MB**——单个进程吃掉了
# 全机 3900MB 的四分之一。原因是题目素材不停往 ~/Desktop 扔文件（跑到这时
# 已经 80 个），它一直在渲染缩略图。
#
# 杀掉之后可用内存从 151MB 回到 1474MB。GNOME Shell 会自动把它拉起来，
# 桌面图标短暂消失后恢复。
DING_RSS_LIMIT_MB = 400


def restart_leaking_desktop_icons(log=print):
    """桌面图标扩展涨太大就重启它。"""
    try:
        out = subprocess.run(["ps", "-eo", "pid,rss,args"],
                             capture_output=True, text=True).stdout
    except Exception:
        return
    for line in out.splitlines():
        if "ding@rastersoft.com" not in line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, rss_kb = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if rss_kb / 1024.0 >= DING_RSS_LIMIT_MB:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
            log("桌面图标扩展涨到 {:.0f}MB，已重启".format(rss_kb / 1024.0))
            time.sleep(3)


def close_stale_apps(log=print):
    """关掉上一题留下的 GUI 应用，把内存和桌面还原到干净状态。"""
    closed = []
    for name in STALE_APPS:
        result = subprocess.run(["pkill", "-x", name], capture_output=True)
        if result.returncode == 0:
            closed.append(name)
    if closed:
        time.sleep(1.5)
        log("关掉残留应用: {}".format(", ".join(closed)))
    restart_leaking_desktop_icons(log=log)


def clean_libreoffice_session(log=print):
    """把 LibreOffice 的崩溃恢复状态清掉。

    和 clean_gimp_session 同一条纪律，触发它的证据同样是实测：pkill 掉 soffice
    之后再起，LibreOffice 会弹"文档恢复"对话框，树里第一屏全是
    `table cell "Not recovered yet"` 之类的恢复列表——而题目要的那张表根本
    还没打开。agent 面对的是一个和题面对不上的桌面。

    libreoffice 三段加起来 117 道题（calc 47 / impress 47 / writer 23），
    不清就是给它们全埋雷。

    恢复列表存在 registrymodifications.xcu 里的 RecoveryList 节点。
    直接删整个 user 目录太粗暴（会连带把题目 config 设的偏好一起清掉），
    所以只摘掉恢复相关的条目。
    """
    for name in ("soffice.bin", "soffice", "oosplash"):
        subprocess.run(["pkill", "-x", name], capture_output=True)
    time.sleep(2)
    path = os.path.expanduser(
        "~/.config/libreoffice/4/user/registrymodifications.xcu")
    if not os.path.exists(path):
        log("LibreOffice 没有配置文件，无需清理")
        return
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        log("LibreOffice 配置读不出来，跳过清理：{}".format(error))
        return
    # RecoveryList 是一个 <item oor:path="/org.openoffice.Office.Recovery..."> 段。
    cleaned = re.sub(
        r'<item oor:path="/org\.openoffice\.Office\.Recovery[^"]*">.*?</item>',
        "", text, flags=re.S)
    if cleaned == text:
        log("LibreOffice 没有待恢复的文档")
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(cleaned)
        log("LibreOffice 恢复列表已清空")
    except OSError as error:
        log("LibreOffice 配置写不回去，跳过：{}".format(error))


def _touches_libreoffice(task):
    blob = json.dumps(task.get("config") or [], ensure_ascii=False).lower()
    return ("soffice" in blob or "libreoffice" in blob
            or any(a.startswith("libreoffice") for a in (task.get("related_apps") or [])))


def clean_gimp_session(log=print):
    """把 GIMP 的崩溃恢复状态清掉，让每道题从确定的状态开始。

    和 clean_chrome_session 是同一条纪律，触发它的证据来自第 47 题的轨迹：
    GIMP 一起来就是个 **"Image Recovery" 模态对话框**——上一道 gimp 题结束时
    被 pkill 掉了，GIMP 把它当成崩溃，于是下一题一开局先弹恢复框。
    agent 点了 Recover，捞回来的图还是**坏的**（只剩顶上一条，其余透明），
    并且作为第三个标签页留在那里。

    在这种状态上跑出来的分数不管高低都是假的：题面说"我的照片"，
    而 GIMP 里躺着三张图，其中一张是残片。gimp 段有 26 道题，
    不清就是给后面 25 道全埋雷。

    官方靠恢复虚拟机快照做到这一点，我们没有那一层，只能手工等价。
    """
    for name in ("gimp", "gimp-2.10", "script-fu"):
        subprocess.run(["pkill", "-x", name], capture_output=True)
    time.sleep(2)
    removed = 0
    # backups/ 是崩溃恢复的素材来源，sessionrc 记着上次的窗口摆位。
    root = os.path.expanduser("~/.config/GIMP/2.10")
    backups = os.path.join(root, "backups")
    if os.path.isdir(backups):
        for name in os.listdir(backups):
            try:
                os.remove(os.path.join(backups, name))
                removed += 1
            except OSError:
                pass
    log("GIMP 会话已清理（删掉 {} 个崩溃恢复备份）".format(removed))


def _touches_gimp(task):
    return "gimp" in json.dumps(task.get("config") or [], ensure_ascii=False).lower() \
        or "gimp" in (task.get("related_apps") or [])


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


def ensure_app_running(app_group, log=print):
    """config 为空的题，目标应用得由我们保证在跑。

    OSWorld 的空 config 意味着"虚拟机快照里这个应用本来就开着"。我们没有快照，
    不补这一步的后果实测过：第 11 题（把 Chrome 界面语言改成一门虚构语言）
    交给 cc 时 Chrome 根本没在跑，它如实回了"Chrome 不在这台机器上"——
    那是对的，但它回答的是另一个问题，而这道题问的是别的。
    """
    launchers = {
        "chrome": ["google-chrome", "--remote-debugging-port={}".format(CHROME_CDP_PORT)],
        "vlc": ["vlc"],
        "thunderbird": ["thunderbird"],
        "gimp": ["gimp"],
        "vs_code": ["code"],
    }
    command = launchers.get(app_group)
    if not command:
        return False
    probe = subprocess.run(["pgrep", "-c", os.path.basename(command[0])],
                           capture_output=True, text=True)
    if (probe.stdout or "0").strip() not in ("", "0"):
        return False
    subprocess.Popen(command, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("  config 为空，替它启动 {}".format(command[0]))
    time.sleep(12)
    return True



def _write_streams(params, done, cache_dir):
    """把命令的 stdout/stderr 按声明写进**判据实际用的那个缓存目录**。

    官方的 execute / command 都支持这个：跑完把输出存成缓存目录里的一个文件，
    后面的 getter 再用 cache_file 去读。实测第 263 题（diff → diff.out）和
    第 269 题（grep/apt → grep.out、apt.out）都靠它。

    两个坑都踩过：
      1. 只给 execute 加、忘了 command——第 269 题照样判 0，
         唯一线索是日志里打的是"命令"不是"执行"。
      2. 写进全局 CACHE_DIR 而不是 LocalEnv.cache_dir（每次新建的临时目录），
         get_cache_file 照样找不到，看起来像"修了但没修"。
    """
    for key in ("stdout", "stderr"):
        name = params.get(key)
        if not name:
            continue
        target = os.path.join(cache_dir or CACHE_DIR, name)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "wb") as handle:
            handle.write((done.stdout if key == "stdout" else done.stderr) or b"")


def apply_config(task, log=print, cache_dir=None):
    """执行一道题的 config 段。返回 (是否就绪, 跳过的步骤说明)。

    不支持的步骤**不静默跳过**：返回原因，让调用方把这道题记成"环境不支持"。
    静默跳过会让 agent 面对一个残缺的环境，然后把布置失败记成它的失败。
    """
    skipped = []
    # 需要切到前台的标签，等 config 全部跑完再处理。见下面 chrome_open_tabs 分支。
    pending_active = []
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
                command = _substitute(params["command"])
                done = subprocess.run(
                    command, shell=not isinstance(command, list),
                    capture_output=True, timeout=180)
                # **stdout / stderr 参数不能忽略。**
                #
                # 官方的 execute 支持把输出写成缓存目录里的一个文件，后面的
                # getter 再用 cache_file 去读它。实测第 263 题就是这样：
                # postconfig 跑 `diff a.pdf b.pdf` 并声明 stdout="diff.out"，
                # 判据 check_list 再去读那个文件。
                #
                # 我原来的实现只跑命令、丢掉输出，于是 get_cache_file 断言
                # 文件存在时直接 AssertionError——而那道题的另一个判据
                # compare_table 已经是 1.0。一次做对的操作被记成 0 分。
                _write_streams(params, done, cache_dir)
                log("  执行: {}".format(str(command)[:90]))
            elif kind == "command":
                # **和 execute 走同一条落盘逻辑。**
                #
                # 我原来只给 execute 加了 stdout 支持，结果第 269 题照样判 0——
                # 那道题的两步 postconfig 用的是 `command` 而不是 `execute`
                # （日志里打的是"命令"不是"执行"，这是唯一的线索）。
                # 两个 kind 在官方那边行为一致，这里也必须一致。
                command = _substitute(params["command"])
                done = subprocess.run(command, shell=isinstance(command, str),
                                      capture_output=True, timeout=180)
                _write_streams(params, done, cache_dir)
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
                    ["google-chrome", "--remote-debugging-port={}".format(CHROME_CDP_PORT)] + urls,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # 目标标签要切到前台，但**不能在这里切**——实测第 7 题：
                # 这一步跑的时候 Chrome 还在加载，CDP 里那个页面要么还不存在、
                # 要么 URL 还是 about:blank，激活自然落空，前台停在新标签页。
                # 记下来，等整个 config 跑完、界面稳定之后再切。
                pending_active.append(urls[-1] if urls else "")
                log("  Chrome 打开 {} 个标签".format(len(urls)))
            elif kind == "chrome_inject_js":
                # 第 8 题的 postconfig 用它把焦点从输入框移开，逼 Chrome 把
                # 改动落盘。不实现就判不了分——又一个"仪器缺一块就把成功记成
                # 失败"的位置。
                _chrome_inject_js(params.get("js") or "")
                log("  注入 JS")
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
    # 很多题的指令写的是 "this site" / "我正在看的这个网页"——那句话是**题面的
    # 一部分**，不是背景描述。前台停在别处，题就变成了另一道题。实测第 5 题：
    # cc 看到 Bing 新标签页，如实汇报"这不是一个网站，我停下来了"，
    # 它没做错任何事，是环境没把题布置对。
    for url in pending_active:
        if url:
            time.sleep(4)
            if not _chrome_activate_tab(url):
                skipped.append("activate_tab({})".format(url[:50]))
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


def evaluate(task, env=None, log=print, retry_on_zero=True):
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
    if post and retry_on_zero:
        log("  跑 postconfig（{} 步）以固化被测状态".format(len(post)))
        apply_config({"config": post}, log=lambda *a: None,
                     cache_dir=env.cache_dir)
        time.sleep(3)

    funcs = _as_list(spec.get("func"))
    if not funcs:
        return None, "这道题没有评估函数"
    if funcs == ["infeasible"]:
        return None, "官方标记为 infeasible（正确行为是拒绝执行），本轮跳过"

    # 判据在**子进程**里跑，为的是把"评估器把机器撑爆了"和"模型没做对"分开。
    #
    # 实测触发它的是第 57 题：官方判据 check_palette_and_structure_sim 要对两张
    # 5184x3888（2000 万像素）的图算结构相似度，中间数组按 float64 算一个就
    # 1.6GB，而这台机器只有 3.8G 内存 + 3.1G swap。整个进程被内核 OOM 杀掉，
    # 退出码 137，跑测脚本连一行输出都没留下——从外面看就像"跑了 25 分钟
    # 卡死了"。我最初也确实按超时去查，还一路查到了截图载荷上，全是错的。
    #
    # 不隔离的后果比查错方向更严重：判分把**调用它的进程**一起带走，
    # 那一题既没有分数也没有记录，而重跑还是同样的结果。隔离之后，
    # 判据炸了就只炸子进程，我们照实记一句"环境不支持判分"——
    # 按既定纪律，环境缺陷不许记成模型失败。
    if _isolate_evaluation:
        return _evaluate_isolated(task, env, retry_on_zero)

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

    # 判 0 分时**等一下再判一次**。
    #
    # 到目前为止这套仪器给过 6 次假阴性，其中多次同一个成因：应用把状态**惰性
    # 刷盘**，判分读到的是刷盘前的旧值。第 1 题（默认搜索引擎）和第 8 题
    # （配置文件用户名）靠官方 postconfig 的 pkill+重启强制落盘躲过去了，
    # 而第 17 题（去掉启动页）的评估器**没有 postconfig**——第一次判 0.0，
    # 几秒后原样再判就是 1.0，磁盘上的值自己变对了。
    #
    # 重判只在 0 分时做，代价 3 秒。它不会把真失败变成通过：只有应用确实在
    # 稍后提交了状态，第二次才会读到不同的值——而那本来就该算通过。
    # 反过来，不重判就会把一次真实的成功记成失败，那是最坏的一种数据污染。
    if score == 0 and retry_on_zero:
        time.sleep(3)
        again, again_detail = evaluate(task, env=env, log=lambda *a: None,
                                       retry_on_zero=False)
        if again is not None and again > 0:
            return again, "{}（第一次判 0，等 3 秒重判：惰性刷盘）".format(again_detail)
    return score, "; ".join(details)


# 判分是否隔离到子进程跑。子进程内部会把它置 False，避免无限递归。
_isolate_evaluation = True


def _evaluate_isolated(task, env, retry_on_zero):
    """在子进程里判分，进程被杀掉时如实报告，而不是把调用方一起带走。"""
    import multiprocessing

    def _child(pipe):
        global _isolate_evaluation
        _isolate_evaluation = False
        try:
            pipe.send(("ok", evaluate(task, env=env, retry_on_zero=retry_on_zero)))
        except BaseException as error:      # noqa: BLE001 - 什么都要报回去
            pipe.send(("err", "{}: {}".format(type(error).__name__, str(error)[:200])))
        finally:
            pipe.close()

    parent, child = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(target=_child, args=(child,))
    process.start()
    child.close()
    payload = None
    try:
        if parent.poll(900):
            payload = parent.recv()
    except EOFError:
        payload = None
    process.join(30)
    if process.is_alive():
        process.terminate()
        process.join(10)

    if payload is None:
        # -9 就是内核 OOM killer 干的。说清楚是环境装不下，不是模型没做对。
        if process.exitcode in (-9, 137):
            return None, ("评估器被内核 OOM 杀掉（本机 3.8G 内存 + 3.1G swap "
                          "装不下官方判据的中间数组）——环境不支持判分，"
                          "不计为模型失败")
        return None, ("评估器子进程异常退出（exitcode={}），没有拿到分数——"
                      "环境问题，不计为模型失败".format(process.exitcode))
    kind, value = payload
    if kind == "err":
        return None, "评估器抛异常：{}".format(value)
    return value


def _normalize_url(url):
    """把 URL 压成能互相比对的形式：去掉协议、开头的 www.、结尾的斜杠。

    题目里写的是 `https://drugs.com`，而 Chrome 真正停在
    `https://www.drugs.com/`——差一个 `www.`。原来的前缀比对两边都对不上，
    于是环境明明布置对了（那个页面就开着），却报"这道题的环境不完整"。
    这类假警报比漏报更坏：它会让人把一次正常的失败当成环境问题放过去，
    也会让一次正常的成功背上"数据存疑"的标签。
    """
    text = str(url or "").strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    return text.rstrip("/")


def _same_page(got, want):
    a, b = _normalize_url(got), _normalize_url(want)
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)
