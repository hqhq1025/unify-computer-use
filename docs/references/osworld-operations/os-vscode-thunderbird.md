# OSWorld 的 os / vs_code / thunderbird 三个 domain 需要哪些 GUI 操作

## 数据来源

- 任务定义：`/home/user/OSWorld/evaluation_examples/examples/{os,vs_code,thunderbird}/*.json`
  - `os` 24 个、`vs_code` 23 个、`thunderbird` 15 个，与 `evaluation_examples/test_all.json` 中三个 domain 的条目数一致。
- 不可行任务名单：`evaluation_examples/test_infeasible.json`，其中 `os` 5 个、`vs_code` 4 个、`thunderbird` 1 个。
- 环境与无障碍实现（用于判断 a11y 可寻址性）：
  - `desktop_env/server/main.py`（AT-SPI 树的构造、终端输出 getter）
  - `desktop_env/server/README.md`（VM 配置说明，含 a11y 开关和 VS Code 评测插件说明）
  - `desktop_env/evaluators/getters/vscode.py`（VS Code 状态是怎么取的）
  - `desktop_env/evaluators/metrics/general.py` 的 `check_accessibility_tree`
  - 任务 `d38192b0` 评测阶段从 HuggingFace 下载的 `show-thunderbird-attachments.py`（运行时脚本，不在仓库里；本文引用的是其实际内容）
- VM 基线：Ubuntu 桌面 + GNOME，VS Code `1.91.1`（`desktop_env/server/README.md` 的软件安装章节）。

## 统计口径

1. **一个任务可以计入多类操作**，所以各类频次之和大于任务总数。
2. 频次按「口径 B：主路径」计数 —— 即按 `instruction` 的自然语义，人在 GUI 上完成它最短路径会碰到的操作面。
3. 另外单独标注「口径 A：强制」—— 指任务的 `config` 或 `evaluator` 在结构上把某个操作钉死了，例如：
   - `config` 里用 `pyautogui.hotkey('ctrl','alt','t')` 预先开好终端并 `activate_window: Terminal`；
   - `evaluator` 的 `result.type` 是 `vm_terminal_output`（必须在可见终端里执行）；
   - `evaluator` 的 `result.type` 是 `accessibility_tree`（必须让目标控件真的出现在 a11y 树里）；
   - `evaluator.postconfig` 里的 `close_window`（必须先关窗口让配置落盘）。
4. **不可行任务（infeasible）单独计一类操作**：「识别任务不可完成并拒答」，同时它们的「本来会走哪个面板」也照常计入对应类别，并标注 `(infeasible)`。
5. a11y 判断分三档，并标注证据强度：
   - `[任务文件直证]`：任务 json 里的 xpath / CSS selector 直接证明该控件可寻址。
   - `[OSWorld 代码直证]`：OSWorld 自身的实现代码证明。
   - `[技术判断]`：我基于技术栈的推断，未在本次数据源中直接验证，已单独标注。
6. 「AT-SPI 可寻址」在本文的严格含义是：**能拿到一个有稳定 `name`（或稳定 `attr:id`）的节点，并且能对它调用 AT-SPI `Action` 接口的语义动作**。只能拿到 bounding box 然后算中心点去点，本文记作「不可寻址，退化为坐标点击」。

## 全局速览

| domain | 任务数 | 不可行 | 可行 | 核心结论 |
| --- | --- | --- | --- | --- |
| os | 24 | 5 | 19 | 19 个可行任务**全部**可以只靠 GNOME Terminal 敲命令完成，没有一个真正必须操作其它 GUI 面板 |
| vs_code | 23 | 4 | 19 | 10/23 围绕「改配置」，判分口径是 JSON 文件；编辑器内文本操作只有 3 个 |
| thunderbird | 15 | 1 | 14 | 判分几乎都读 profile 里的配置文件，且 postconfig 先 `close_window` 触发落盘，因此**必须走 GUI**，外部改文件会被覆盖 |

---

# 一、os domain（24 个任务）

涉及三个 GUI 面：GNOME Terminal、文件管理器 Nautilus、GNOME 设置 / Shell 顶栏 / Dock。

## 1.1 必须的操作清单（按频次从高到低）

### ① 终端：打开终端窗口 + 输入命令 + 回车 + 读回显 —— 19/19 个可行任务

- **强制（口径 A）8 个**：`4127319a`、`13584542`、`37887e8c`、`4d117223`、`5c1075ca`、`5ced85fc`、`6f56bf42`、`4783cc41`(infeasible)
  - 前两个是评测强制：`4127319a` 的 `evaluator.result.type` 是 `vm_terminal_output`；`13584542` 的 `postconfig` 用 pyautogui 重开终端跑 `stty size` 并期望输出含 `43 132`。
  - 其余 6 个是 `config` 强制：里面有 `pyautogui.hotkey('ctrl','alt','t')` + `{"type":"activate_window","parameters":{"window_name":"Terminal"}}`。
- **典型形态**：`Ctrl+Alt+T` 开终端 → 逐字符输入一条 shell 命令（`find`/`cp`/`chmod`/`sed`/`tar`/`gsettings`）→ 回车 → 从终端回显里读结果。
- **a11y 判断：读可寻址，写只能靠键盘。**
  - GNOME Terminal（VTE/GTK3）在 AT-SPI 里有 `terminal` role 节点，整屏文本可读 —— `desktop_env/server/main.py` 的 `get_terminal_output()` 就是用
    `//application[@name="gnome-terminal-server"]/frame[@st:active="true"]//terminal[@st:focused="true"]` 取 `.text`。`[OSWorld 代码直证]`
  - 但 `terminal` 节点没有「输入这段文字」的语义 action，命令只能通过键盘事件打进去；且该 xpath 要求 frame `st:active` 且 terminal `st:focused`，**窗口失焦就取不到输出**，这对 agent 是个真实约束。`[OSWorld 代码直证]`

### ② 文件与目录操作（复制 / 移动 / 重命名 / 压缩 / 改权限 / 回收站还原）—— 9 个任务

- `23393935`（递归把 photos 下的 .jpg 复制到 cpjpg）、`5c1075ca`（保留目录层级复制 `*failed.ipynb`）、`37887e8c`（按 mtime 分流并压缩）；另有 `4d117223`(chmod 644)、`6f56bf42`(一份文件复制到三个目录)、`5ced85fc`(逐行追加 `<br/>` 写 output.txt)、`e0df059f`(重命名目录)、`5ea617a3`(从回收站恢复)、`4783cc41`(infeasible)。
- **典型形态**：要么在终端里一条命令搞定，要么在 Nautilus 里浏览目录树 → 多选 → `Ctrl+C`/`Ctrl+V` / `F2` 重命名 / 右键「还原」。
- **a11y 判断：Nautilus 侧基本可寻址。** GTK 的文件列表在 AT-SPI 里是 table/list，每个文件是有 `name` 的 item；右键菜单项是 `menu-item` 且带 action。`[技术判断]`
- **坑**：`e0df059f` 的 `config` 是 `echo {CLIENT_PASSWORD} | sudo -S mkdir ~/Desktop/todo_list_Jan_1`，目录属 root，**在 Nautilus 里 F2 重命名会因权限失败**，必须 `sudo mv`。这是一个只看 instruction 看不出来的陷阱。`[任务文件直证]`

### ③ GNOME 设置面板（gnome-control-center）—— 8 个任务（6 可行）

- `28cc3b7e`（声音→音量拉满）、`3ce045a0`（辅助功能→大号文本 / 放大镜）、`a4d98375`（隐私→屏幕锁定）、`b6781586`（日期和时间→时区 UTC+0）、`bedcedc4`（电源→关闭「不活动时调暗屏幕」）、`f9be0997`（通知→勿扰）、`b3d4a89c`(蓝牙, infeasible)、`fe41f596`(电源→显示电量百分比, infeasible)。
- **典型形态**：打开「设置」→ 左侧列表选面板 → 拨一个 switch / 拖一个 slider / 选一个下拉项。
- **判分口径全部是 gsettings 或 shell 命令**，例如 `bedcedc4` 的 `evaluator` 是 `gsettings get org.gnome.desktop.session idle-delay == "uint32 0"` **或** `org.gnome.settings-daemon.plugins.power idle-dim == "false"`（`conj: "or"`），说明作者也承认存在多条等价路径。`[任务文件直证]`
- **a11y 判断：可寻址但 name 不稳定。** GTK 的 `toggle-button`/`switch` 有 action，但 GNOME Settings 大量使用 `GtkListBoxRow` + 标题 label + 右侧开关的结构，**开关自身的 accessible name 经常为空**，需要靠「同一 row 里的 label 文本」来定位再取兄弟节点。`[技术判断]`

### ④ 文件管理器 Nautilus 作为主界面 —— 4 个任务

- `5ea617a3`（回收站 → 找到 `poster_party_night.webp` → 右键「还原」）、`e0df059f`（重命名）、`23393935`（浏览多层 photos 目录）、`4127319a`（`config` 用 `xdg-open /home/user/project`，**任务起始画面就是 Nautilus 窗口**）。`[任务文件直证]`
- **典型形态**：侧边栏切换位置（回收站 / 主目录）→ 双击进入子目录 → 选中项 → 右键上下文菜单。
- **a11y 判断：中等偏好，见 ② 的说明。** 但「回收站还原」这类动作藏在上下文菜单里，需要先触发右键才会出现在 a11y 树中 —— 未展开的菜单在树里不存在。`[技术判断]`

### ⑤ GNOME Shell 顶栏快捷设置 / 通知面板 —— 2 个任务

- `28cc3b7e`（点右上角状态区 → 拖音量滑块到底）、`f9be0997`（点顶栏时钟 → 通知面板 → 「勿扰」开关）。
- **a11y 判断：高风险，很可能只能坐标点击。** gnome-shell 是 Clutter/St 自绘 UI，靠 Cally 桥接到 AT-SPI，暴露的节点远比 GTK 应用稀疏，很多按钮 `name` 为空。`[技术判断]` —— 注意这两个任务都有等价的命令行路径（`pactl` / `gsettings`），所以实践上可以绕开。

### ⑥ Dock 收藏夹右键菜单 —— 1 个任务

- `ec4e3f68`（从 favorites 里移除 vim）。典型形态：在左侧 Dock 上右键 vim 图标 → 「从收藏夹中移除」。
- **a11y 判断：与 ⑤ 同源（Ubuntu Dock 是 gnome-shell 扩展），高风险。** 等价命令：`gsettings set org.gnome.shell favorite-apps "[...]"`。`[技术判断]`

### ⑦ 应用商店安装软件 —— 1 个任务

- `94d95f96`（安装 Spotify），判分是 `which spotify`。GUI 路径是 Ubuntu Software 搜索 + 点 Install（需等待下载）；命令行路径 `sudo snap install spotify`。

### ⑧ 终端首选项对话框 —— 1 个任务

- `13584542`（把终端尺寸 132x43 持久化）。GUI 路径：终端菜单 → Preferences → Profile → Text → 初始尺寸；命令行路径：写 `org.gnome.Terminal.Legacy.Profile` 的 `default-size-columns`/`default-size-rows`。

### ⑨ 识别不可完成并拒答 —— 5 个任务

- `b3d4a89c`（VM 没有蓝牙硬件）、`fe41f596`（VM 没有电池）、`c288e301`（不存在 Python4）、`a462a795`（用户 charles 不存在，且 instruction 明确禁止创建）、`4783cc41`（`$sourceDir`/`$targetDir` 未定义）。

## 1.2 「命令行就能做完」vs「必须走 GUI」

> 这里的「命令行」指 **agent 在 GUI 终端窗口里敲命令**（OSWorld 的动作空间是 pyautogui 键鼠），不是走 OSWorld backend 的 `execute` 通道。

| 分类 | 数量 | 任务 |
| --- | --- | --- |
| **只需终端**（不必碰任何其它 GUI 面板） | 8 | `23393935`、`37887e8c`、`4127319a`、`4d117223`、`5812b315`、`5c1075ca`、`5ced85fc`、`6f56bf42` |
| **双路径**（任务语义指向某个 GUI 面板，但终端同样能满足判分） | 11 | `13584542`、`28cc3b7e`、`3ce045a0`、`5ea617a3`、`94d95f96`、`a4d98375`、`b6781586`、`bedcedc4`、`e0df059f`、`ec4e3f68`、`f9be0997` |
| **必须走非终端 GUI** | **0** | — |
| 不可行 | 5 | 见 ⑨ |

**结论：os domain 里没有任何一个可行任务是终端做不到的。** 所有 19 个可行任务的 `evaluator` 读的都是文件系统状态、`gsettings`/`pactl`/`timedatectl` 的输出，或终端回显 —— 没有一个去检查「你是不是真的点了那个开关」。

两个需要注意的例外形态：

- `4127319a` 的判分读 `vm_terminal_output`，所以**结果必须显示在一个处于活动且聚焦状态的 GNOME Terminal 窗口里**，把命令跑在别处不算数。
- `5812b315`、`b6781586`、`e0df059f`、`94d95f96` 需要 sudo；密码是 `password`，会触发终端里的密码提示（无回显）或 GUI 的 polkit 认证弹窗。

---

# 二、vs_code domain（23 个任务）

所有 23 个任务的 `config` 都是 `{"type":"launch","command":["code", ...]}` + `{"type":"activate_window","window_name":"Visual Studio Code"}`，即**起始状态一定是 VS Code 已打开并聚焦**。

## 2.1 必须的操作清单（按频次从高到低）

### ① 改配置：设置界面 / 直接编辑 settings.json —— 10 个任务（7 可行 + 3 infeasible）

- 可行且判分读 `~/.config/Code/User/settings.json` 的 7 个：
  - `276cc624`（`editor.wordWrapColumn: 50`）、`70745df8`（`files.autoSave: afterDelay` + `files.autoSaveDelay: 500`）、`9439a27b`（`debug.focusEditorOnBreak: false`）、`9d425400`（`workbench.editor.wrapTabs: true`）、`c6bf789c`（`files.exclude` 加 `**/__pycache__`）、`e2b5e914`（`python.analysis.diagnosticSeverityOverrides.reportMissingImports: "none"`）、`982d12a5`（`workbench.colorTheme: "Visual Studio Dark"`）。
- 另有 3 个 infeasible 也是「用户以为能改设置」：`7c4cc09e`（不装扩展改界面语言为阿拉伯语）、`971cbb5b`（不装扩展让每次启动自动建 test.py）、`dcbe20e8`（把 VS Code 背景换成图片）。
- **典型形态**：`Ctrl+,` 打开 Settings UI → 搜索框输入设置名 → 勾 checkbox / 填数字 / 选下拉；或 `Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)` → 在编辑器里直接写 JSON。
- **关键观察**：判分口径是 JSON 文件而不是 UI 状态，**「打开 settings.json 编辑文本」是比「在设置 UI 里找开关」更稳的路径**，尤其是 `c6bf789c` / `e2b5e914` 这种嵌套对象的设置（Settings UI 里它们会退化成「Edit in settings.json」链接）。`982d12a5` 的 `config` 还预先把主题写成了 `Red`，所以 settings.json 一定已存在。`[任务文件直证]`
- **a11y 判断：Settings UI 可寻址（DOM→ARIA→AT-SPI），编辑器内不可寻址。** 详见第四节。

### ② 文件 / 文件夹 / 工作区对话框（GTK file chooser）—— 6 个任务（5 可行）

- `53ad5833`（File → Open Folder 打开 `/home/user/project`）、`57242fad`（新建 test.py 并保存到 Desktop）、`5e2d93d8`（Save Workspace As → `/home/user/project/project.code-workspace`）、`6ed0a554`（Add Folder to Workspace，`data1` + `data2` 两次）、`0512bb38`（Install from VSIX 选 `/home/user/test.vsix`）、`847a96b6`(infeasible)。
- **典型形态**：VS Code 菜单栏 File → 某个条目 → 弹出 GTK 文件选择器 → 在路径栏 `Ctrl+L` 输入路径或逐级点目录 → 确认。
- **a11y 判断：分裂的。** VS Code 自身的菜单是 Electron 渲染的（DOM），而文件选择器是 GTK 原生对话框（走 XDG portal 或 GTK），**后者的 a11y 支持明显好于前者**，路径输入框、Places 侧栏、文件列表都有 name。这意味着同一条操作链上要跨两套 a11y 语义。`[技术判断]`
- `6ed0a554` 需要**重复两次**同一操作，且判分要求 `.code-workspace` 里 `folders` 顺序是 `project, data1, data2`。`[任务文件直证]`

### ③ 命令面板 `Ctrl+Shift+P` —— 通用入口，1 个任务被 OSWorld 自己强制使用

- 几乎每个 vs_code 任务都能用命令面板走通（打开设置、装 VSIX、选主题、打开键盘快捷键页……）。
- **OSWorld 代码直证**：`desktop_env/evaluators/getters/vscode.py` 里 `get_vscode_config()` 的取数方式就是
  `hotkey(ctrl+shift+p)` → `typewrite(vscode_extension_command)` → `press(enter)`，用来触发自研评测扩展。`53ad5833` 用的正是这个 getter（`vscode_extension_command: "OpenProject"`）。
- **典型形态**：`Ctrl+Shift+P` → 打字模糊搜索 → 回车选第一项。
- **a11y 判断：快速选取器（quick pick）是虚拟列表，只渲染可见项**，靠 a11y 遍历候选项不可靠；但因为它本质是「打字 + 回车」，**用键盘就够了，不需要寻址**。这反而是 VS Code 里最稳的一条路。

### ④ 扩展视图（搜索 / 安装 / 从 VSIX 安装）—— 3 个任务

- `eabc805a`（装 Python 扩展）、`4e60007a`（装 autoDocstring）、`0512bb38`（从本地 `.vsix` 装）。判分统一是 `code --list-extensions | grep <id>`。
- **典型形态**：`Ctrl+Shift+X` 打开扩展侧栏 → 搜索框输入名字 → 在结果卡片上点 Install → 等待安装完成。
- **注意**：`4e60007a` / `eabc805a` 需要真实网络访问 marketplace；`0512bb38` 是本地文件，走「扩展视图右上角 `...` → Install from VSIX」。
- **a11y 判断：扩展列表是虚拟滚动**，未进入视口的条目不在 a11y 树里；Install 按钮有 ARIA label，开启无障碍后通常有 name。`[技术判断]`

### ⑤ 菜单栏 File 菜单 —— 4 个任务

- `53ad5833`、`5e2d93d8`、`6ed0a554`、`57242fad`。都要展开 VS Code 自绘的菜单栏（不是原生 GTK 菜单）。
- **a11y 判断：Electron 自绘菜单在 AT-SPI 里表现为普通 DOM 节点树，role 可能不是标准 `menu`/`menu-item`**，`Action` 接口未必可用，展开动作常常只能靠点击或 `Alt+F` 键盘序列。`[技术判断]`

### ⑥ 编辑器内文本编辑（查找替换 / 多行缩进 / 保存）—— 3 个任务

- `0ed39f63`（把文档里所有 `text` 替换为 `test`，`Ctrl+H`）、`ec71221e`（把第 2–10 行整体缩进一个 tab）、`57242fad`（新建文件并保存）。
- **典型形态**：`Ctrl+H` 填两个输入框 → Replace All；或 `Ctrl+G` 跳到第 2 行 → `Shift+Ctrl+G`/`Shift+↓` 选到第 10 行 → `Tab`。
- **兜底细节**：`0ed39f63` 和 `ec71221e` 的 `evaluator.postconfig` 都会**替 agent 按一次 `Ctrl+S`**，说明作者知道「忘记保存」是高频失败模式。`[任务文件直证]`
- **a11y 判断：这是 VS Code 里最不可寻址的部分。** Monaco 编辑器默认只把整个编辑区暴露成一个 `textbox`，不逐行暴露内容（除非开 `editor.accessibilitySupport: on` 的屏幕阅读器模式）。**「定位第 2 行到第 10 行」在 a11y 层没有对应节点**，只能靠 `Ctrl+G` 跳行这类键盘操作。`[技术判断]`

### ⑦ 键盘快捷键设置（keybindings.json）—— 2 个任务

- `930fdb3b`（新增 `ctrl+j` → `workbench.action.focusActiveEditorGroup`，`when: terminalFocus`）、`ea98c5d7`（移除 `ctrl+f` 的 `list.find`，判分期望写入 `"command": "-list.find"`）。
- **典型形态**：`Ctrl+K Ctrl+S` 打开 Keyboard Shortcuts → 搜索 → 右键 Change/Remove Keybinding → **在录制框里按下实际按键**；或直接编辑 `keybindings.json`。
- **坑**：`ea98c5d7` 期望的是**带 `-` 前缀的 negative keybinding**，这只有走 UI 的「Remove Keybinding」或手写 JSON 才能得到；`930fdb3b` 的录制框会吞掉按键，用 UI 录制反而比写 JSON 难。`[任务文件直证]`

### ⑧ 侧边栏 / 调试变量面板 —— 1 个任务

- `7aeae0e2`（可视化当前 python 文件里的所有 numpy 数组）。**这是 vs_code domain 里唯一用 a11y 树判分的任务**：`evaluator.result.type = "accessibility_tree"`，`include` 要求树里同时出现 `VARIABLES`、`X`、`idx`、`means`、`confint`，`exclude` 掉 `SyntaxError`/`Traceback`。`[任务文件直证]`
- **典型形态**：装/用 Python 扩展 → 起一个调试会话或 Jupyter 交互窗口 → 让 VARIABLES 面板列出这几个变量。
- **意义**：这个任务同时证明了 **VS Code 的 Electron a11y 树在开启后确实能读到侧边栏文本内容**，否则这条判分规则不可能成立。

### ⑨ 识别不可完成并拒答 —— 4 个任务

- `7c4cc09e`（不用扩展改显示语言为阿拉伯语）、`847a96b6`（同一窗口同时打开两个 workspace）、`971cbb5b`（不用扩展让启动时自动建 test.py）、`dcbe20e8`（把背景改成 Downloads 里的照片）。
- 这四个的共同点是**看起来像是「改个设置」，实际上 VS Code 没有该能力** —— 对 agent 来说是最容易误判成「我在设置里翻不到，再翻一会儿」的一类。

## 2.2 vs_code 的判分口径特点

| 判分方式 | 任务数 | 说明 |
| --- | --- | --- |
| 读 `settings.json` | 7 | `check_json_settings` / `compare_config` |
| 读 `keybindings.json` | 2 | `check_json_keybindings` |
| 跑 shell 检查副作用 | 5 | `code --list-extensions`、`ls <dir> \| grep <file>` |
| 比对文件内容 | 2 | `compare_text_file`，postconfig 会替按 `Ctrl+S` |
| 读 a11y 树 | 1 | `7aeae0e2` |
| 走自研 VS Code 扩展 | 1 | `53ad5833`，命令面板 → 扩展导出 → 读文件 |
| infeasible | 4 | — |

**要点：19 个可行任务里有 9 个的判分只看磁盘上的 JSON 文件。** 这意味着「在 VS Code 里打开对应 JSON 文件用编辑器改」是一条通用且比 GUI 控件更稳的路径 —— 而这条路径几乎不依赖 a11y 寻址，只依赖键盘。

---

# 三、thunderbird domain（15 个任务）

15 个任务的 `config` 完全同构：下载一个 `thunderbird-profile*.tar.gz` → 解压到 `/home/user/` → `launch /usr/bin/thunderbird`。profile 目录固定为 `/home/user/.thunderbird/t5q2a5hp.default-release/`，账户是 `anonym-x2024@outlook.com`（IMAP `outlook.office365.com`）。

## 3.1 必须的操作清单（按频次从高到低）

### ① 账户设置对话框 / 账户向导 —— 5 个任务（4 可行）

- `3f28fe4f`（设置纯文本签名「Anonym / XYZ Lab」，判分 `mail.identity.id1.htmlSigText`）、`f201fbc3`（关闭回复时自动引用，判分 `mail.identity.id1.auto_quote: false`）、`dfac9ee8`（移除账户 `anonym-x2024@outlook.com`）、`15c3b339`（新建 outlook 账户，只填地址和密码不提交）、`a1af9f1c`(infeasible，只发不收的账户)。
- **典型形态**：汉堡菜单 ≡ → Account Settings → 左树选中账户下的某个节点（Composition & Addressing / Server Settings）→ 改文本框或复选框 → 关窗口触发落盘；移除账户走 Account Actions → Remove Account → 确认对话框。
- **a11y 判断：可寻址。** `15c3b339` 的判分直接是两条 xpath：
  `//application[@name='Thunderbird']//*[contains(text(),'anonym-x2024@outlook.com') or contains(@name,'anonym-x2024@outlook.com')]`
  和 `//application[@name='Thunderbird']//*[contains(@name,'password') or contains(@name,'Password')]`
  —— 证明账户向导里输入的文本和密码框的 name 都能从 AT-SPI 树里拿到。`[任务文件直证]`

### ② 文件夹面板（folder pane）—— 5 个任务

- `a10b69e1`（新建两个本地文件夹 COMPANY / UNIVERSITY）、`5203d847`（新建本地文件夹 Promotions）、`3f49d2cc`（切换成统一收件箱，判分 `xulstore.json` 里 `folderTree` 的 `mode` 匹配 `\bsmart\b`）、`9bc3cc16`（定位 Inbox）、`dd84e895`（定位 Local Folders 下的 Bills）。
- **典型形态**：在左侧文件夹树上右键 Local Folders → New Folder… → 弹出小对话框输名字 → 确认；或 View → Folders → Unified 切换树的显示模式。
- **a11y 判断：可寻址。** Gecko 会把 XUL 的 `id` 映射成 AT-SPI 的 object attribute，OSWorld 的 README 里给出的示例选择器就是
  `application[name=Thunderbird] page-tab-list[attr|id="tabmail-tabs"]>page-tab[name="About Profiles"]`。`[OSWorld 代码直证]`

### ③ 消息过滤器对话框（Message Filters）—— 3 个任务

- `5203d847`（主题含 `discount` → 移动到 Local Folders/Promotions）、`9b7bc335`（所有邮件 → 转发到 `anonym-x2024@gmail.com`）、`08c73485`（让过滤器也自动作用于子文件夹）。
- 前两个的 `evaluator.postconfig` 是 `{"type":"close_window","window_name":"Message Filters","strict":true}` —— **必须先关掉过滤器对话框，`msgFilterRules.dat` 才会落盘**。`[任务文件直证]`
- **典型形态**：多层嵌套对话框。Tools → Message Filters → 选账户 → New… → 填规则名 → 条件行选「Subject / contains / discount」→ 动作行选「Move Message to / 选目标文件夹」→ OK → 关闭 Message Filters 窗口。这是三个 domain 里**单任务操作步数最多的一类**。
- **a11y 判断：可寻址但极易走错。** 条件行/动作行是动态生成的下拉组合，每次选择都会改变后续控件的结构，靠一次性快照的 a11y 树规划全部步骤不现实，需要边做边重新取树。`[技术判断]`

### ④ 首选项 / 配置编辑器（about:config）—— 2 个任务，但可覆盖更多

- `08c73485` 必须用它：判分要求 `mail.server.default.applyIncomingFilters == true` **且** `mail.imap.use_status_for_biff == false`，这两个 pref 在正常 UI 里没有开关。`[任务文件直证]`
- `10a730d5`（完整深色模式，判分 `extensions.activeThemeID` 匹配 `dark`）。
- **典型形态**：Settings → General → 页面最底部 Config Editor… → 接受警告 → 搜索 pref 名 → 双击切换布尔值 / 填值。
- **可覆盖更多**：`3f28fe4f`（`mail.identity.id1.htmlSigText`）和 `f201fbc3`（`mail.identity.id1.auto_quote`）的判分也是读 pref，理论上都能用配置编辑器直接写，避开账户设置对话框的控件寻址。
- **a11y 判断：配置编辑器是一张虚拟滚动的表**，只有可见行在树里；但它的交互模式是「搜索框打字 + 双击/回车」，键盘就够。

### ⑤ 邮件列表（message list）批量选择与标记 / 另存 —— 2 个任务

- `dd84e895`（给 Bills 文件夹里每封邮件加星标，判分直接查 `global-messages-db.sqlite` 的 `messageAttributes`）、`9bc3cc16`（把收件箱邮件逐封导出成 `.eml` 到 `~/emails.bak`）。
- **典型形态**：点进文件夹 → 在列表里 `Ctrl+A` 全选 → 按 `s` 加星标 / 右键 → Save As… → 选目录。
- **a11y 判断：这是 thunderbird 里最危险的一类。** 邮件列表是虚拟化的（新版是 `<tr is="thread-row">`，老版是 XUL tree），**只有可见行在 AT-SPI 树里**。「给所有邮件加星标」如果按 a11y 树遍历会漏掉未滚动到的行，**必须用 `Ctrl+A` 这种键盘全选语义**。`[技术判断]`

### ⑥ 撰写窗口 + 附件 —— 1 个任务

- `d38192b0`（把 `~/aws-bill.pdf` 附加到已打开的撰写窗口，且不许关闭或发送）。`config` 用 `thunderbird -compose "from=...,to=...,subject='New-month AWS Bill',body=..."` 预先打开撰写窗口。
- **典型形态**：撰写窗口 → Attach 按钮（或 `Ctrl+Shift+A`）→ GTK 文件选择器 → 选 pdf → 打开。
- **a11y 判断：撰写窗口可寻址，且有本次调研中最硬的证据。** 判分脚本 `show-thunderbird-attachments.py` 的实际实现：
  - 用 `wnd.name == "Write: {subject} - Thunderbird"` 找窗口；
  - 用 CSS 选择器 `panel[attr|id="attachmentArea"]>list-box[attr|id="attachmentBucket"]` 找附件栏；
  - 用 `list-item[name^="aws-bill .pdf"]` 判断附件是否存在（注意 name 是 `" ".join(os.path.splitext(name))`，**文件名和扩展名之间被插入了一个空格**，这是 Gecko 的命名怪癖）。
  `[任务文件直证 + 运行时脚本直证]`
  - **但同一个脚本在附件栏折叠时，是取 `push-button[name*="Attachment"]` 的 `cp:screencoord` + `cp:size` 算中心点，然后 `pyautogui.click(x, y)`** —— 即 **OSWorld 官方脚本自己都没有调用 AT-SPI 的 `Action` 接口，而是回落到坐标点击**。这是「能读到节点 ≠ 能调用语义动作」的直接例证。

### ⑦ 附加组件与主题管理器 —— 1 个任务

- `10a730d5`（启用完整深色模式）。典型形态：≡ → Add-ons and Themes → Themes → 在 Dark 主题上点 Enable。

### ⑧ 隐藏菜单栏 / 汉堡菜单导航 —— 几乎所有任务的前置步骤

- Thunderbird 默认**不显示菜单栏**，Tools / View / File 这些入口要么按 `Alt` 临时唤出，要么走右上角 ≡ 汉堡菜单。
- **a11y 判断：未展开的菜单不在 a11y 树里。** OSWorld 的 agent 侧过滤器 `mm_agents/accessibility_tree_wrap/heuristic_retrieve.py` 会丢弃 `st:showing != true` 或 `st:visible != true` 的节点，所以「先展开才能看见」是硬约束 —— agent 无法从一张静态快照里规划出「Tools → Message Filters」这条路径。`[OSWorld 代码直证]`

### ⑨ 识别不可完成并拒答 —— 1 个任务

- `a1af9f1c`（配置一个只发信、不配置收信服务的账户）。

## 3.2 thunderbird 的判分口径：为什么「必须走 GUI」

| 任务 | 判分读什么 | postconfig |
| --- | --- | --- |
| `08c73485` `10a730d5` `3f28fe4f` `f201fbc3` | `prefs.js` | `close_window Mail.thunderbird` |
| `3f49d2cc` | `xulstore.json` | `close_window Mail.thunderbird` |
| `dd84e895` | `global-messages-db.sqlite` | `close_window Mail.thunderbird` |
| `5203d847` `9b7bc335` | `msgFilterRules.dat` | `close_window Message Filters` |
| `dfac9ee8` | `firefox_decrypt.py` 导出的账户 CSV | 下载并跑解密脚本 |
| `9bc3cc16` | `ls -R ~/emails.bak` | — |
| `a10b69e1` | `ls -R .../Mail/Local Folders` | — |
| `d38192b0` | a11y 树（经运行时脚本） | 装 cssselect + 跑脚本 |
| `15c3b339` `7b1e1ff9` | a11y 树 | — |

**关键结论**：8/14 个可行任务的判分先 `close_window` 再读 profile 文件。Thunderbird 在运行期间把 prefs 保存在内存里，退出时才写回 `prefs.js` —— 所以**从外部（终端）改配置文件会在关闭时被内存态覆盖**。这与 os / vs_code 完全相反：

- os：终端能做完全部 19 个可行任务。
- vs_code：9/19 可以靠改磁盘 JSON 绕过 GUI 控件。
- **thunderbird：绕不过去，必须真的在 GUI 里操作**（唯二例外是 `9bc3cc16` 导出 eml 和 `a10b69e1` 建本地文件夹目录 —— 但后者还需要对应的 `.msf` 索引文件，纯 `mkdir` 未必满足 `check_list` 的正则）。

---

# 四、Electron 与 XUL/Gecko 在 Linux AT-SPI 上的支持情况

## 4.1 共同前提：a11y 是全局按需开启的

`desktop_env/server/README.md`（「About the Converted Accessibility Tree」一节）明确写道：

> For several applications like Firefox or Thunderbird, you should first enable
> `gsettings set org.gnome.desktop.interface toolkit-accessibility true`
> to see their accessibility tree.

`[OSWorld 代码直证]`

这个开关最终反映为 D-Bus 上 `org.a11y.Status.IsEnabled`。**Gecko 和 Chromium 都是「检测到该开关/有 AT 客户端才构建无障碍树」**，否则在 AT-SPI 里只能看到一个几乎空的顶层窗口壳。所以：

- 任何基于 a11y 的 agent，**第一步必须确认这个开关是开的**，否则 VS Code 和 Thunderbird 会表现为「有窗口但里面什么都没有」。
- 开启后两者都有可观的**启动延迟和内存开销**（要为整棵 DOM 建映射），大窗口下取一次全树是秒级操作。

## 4.2 Thunderbird（XUL/Gecko）

**支持情况：三者中最好。** Gecko 的 a11y 实现成熟（NVDA/Orca 长期依赖它），XUL 控件映射到标准 AT-SPI role，并且 **XUL 元素的 `id` 会作为 object attribute 暴露**，这给了非常稳定的定位锚点。

本次数据源里的直接证据：

| 证据 | 出处 | 说明 |
| --- | --- | --- |
| `page-tab-list[attr\|id="tabmail-tabs"]>page-tab[name="About Profiles"]` | `7b1e1ff9` 的 evaluator + README | XUL id + role + name 三者都可用 |
| `panel[attr\|id="attachmentArea"]>list-box[attr\|id="attachmentBucket"]`、`list-item[name^="aws-bill .pdf"]` | `d38192b0` 的运行时判分脚本 | 撰写窗口内部结构完全可寻址 |
| `//application[@name='Thunderbird']//*[contains(@name,'password')]` | `15c3b339` 的 evaluator | 表单控件 name 可用 |

**已知问题：**

1. **官方脚本自己都退化成坐标点击。** `show-thunderbird-attachments.py` 找到 `push-button[name*="Attachment"]` 之后，不是调 AT-SPI `Action`，而是读 `cp:screencoord` / `cp:size` 算中心点再 `pyautogui.click`。可寻址 ≠ 可调用语义动作。
2. **文本里混入 `￼`。** `desktop_env/server/main.py` 在取 `queryText()` 后专门 `text.replace("￼","").replace("�","")`，注释写明 "appeared in thunderbird … Object Replacement Character"。做文本匹配前必须清洗。`[OSWorld 代码直证]`
3. **name 的构造有怪癖**，如附件项的 name 是 `aws-bill .pdf`（文件名与扩展名之间有空格），不能假设 name == 用户看到的字符串。
4. **邮件列表和配置编辑器是虚拟滚动**，树里只有可见行。
5. **菜单栏默认隐藏**，未展开的菜单不在树里。

## 4.3 VS Code（Electron/Chromium）

**支持情况：开启后可用，但覆盖不均匀。** Chromium 会把 DOM + ARIA 映射成 AT-SPI，所以 workbench 的侧边栏、列表、设置页大体能读到。

本次数据源里的直接证据：

- `7aeae0e2` 的 evaluator 用 `accessibility_tree` 判分，要求树里出现 `VARIABLES`、`X`、`idx`、`means`、`confint` —— **证明调试变量面板的文本确实进得了 AT-SPI 树**。`[任务文件直证]`

**已知问题（按严重度排序）：**

1. **OSWorld 官方不信任用 a11y 读 VS Code 内部状态。** `desktop_env/server/README.md` 写明：「To extract relevant internal information and configurations from the VS Code environment, we principally leverage the capabilities offered by the VS Code Extension API」，并要求装一个自研的 `eval-0.0.1.vsix`；`desktop_env/evaluators/getters/vscode.py` 则用 `Ctrl+Shift+P` + 打字来触发它。**如果 a11y 够用，作者不需要写扩展。** 这是本文对 Electron a11y 最有力的间接证据。`[OSWorld 代码直证]`
2. **Monaco 编辑器内容不逐行暴露。** 默认只暴露一个 `textbox`（配合有限的行缓冲），行/列/选区没有对应节点。`ec71221e`（第 2–10 行缩进）这类任务在 a11y 层根本没有可寻址目标，只能 `Ctrl+G` 跳行 + `Shift` 选择。`[技术判断]`
3. **大量虚拟滚动**：扩展列表、设置项列表、快速选取器都只渲染可见项。
4. **自绘菜单栏**不是原生 GTK 菜单，role 映射未必标准，`Action` 接口未必可用。
5. **树体量大 + 有截断**：`desktop_env/server/main.py` 里 `MAX_DEPTH = 50`、`MAX_WIDTH = 1024`，深层或宽列表会被截断。`[OSWorld 代码直证]`
6. **VS Code 内的 GTK 文件对话框是另一套 a11y 语义**，同一条操作链要跨两个体系。

## 4.4 GNOME 原生栈（对照组）

- **GTK 应用（Nautilus、gnome-control-center、GNOME Terminal、文件选择器）**：AT-SPI 一等公民，role/name/action 齐全，是三个 domain 里最好定位的部分。`[技术判断]`
- **gnome-shell（顶栏快捷设置、Dock、通知面板）**：Clutter/St 自绘，经 Cally 桥接，暴露远比 GTK 稀疏，很多按钮 name 为空。`os` domain 里涉及它的 3 个任务（`28cc3b7e`、`f9be0997`、`ec4e3f68`）**都有等价的命令行路径**，实践上建议绕开。`[技术判断]`

---

# 五、最可能在 a11y 层出问题的点

1. **「能读到节点」和「能调用语义动作」是两回事。** 最硬的证据是 OSWorld 自己的 `show-thunderbird-attachments.py`：它成功用 CSS 选择器定位到了 `push-button[name*="Attachment"]`，然后仍然读 `cp:screencoord` + `cp:size` 算中心点用 `pyautogui.click` 点下去。如果一个统一的 computer-use 抽象层承诺「找到元素就能 invoke」，在 Gecko/Electron 上会大量落空，必须设计成「a11y 负责定位 + 坐标/键盘负责执行」的混合模式。

2. **虚拟滚动 + 隐藏菜单让「静态快照规划」失效。** 邮件列表、扩展列表、设置列表、快速选取器都只把可见项放进树；Thunderbird 的菜单栏默认隐藏，未展开的菜单在树里不存在；而 OSWorld 的过滤器（`heuristic_retrieve.py`）还会主动丢掉 `st:showing != true` 的节点。后果是：agent 无法从一张快照里规划出完整路径，且「对所有邮件加星标」这类批量操作必须改用 `Ctrl+A` 的键盘语义而不是遍历节点。

3. **Monaco 编辑器是 a11y 盲区。** VS Code 里的文本编辑（`ec71221e` 的行范围缩进、`0ed39f63` 的查找替换）在 AT-SPI 里没有行级节点可寻址，只能退化为纯键盘操作（`Ctrl+G` / `Ctrl+H` / `Shift+方向键`）。任何依赖「先定位到第 N 行元素再操作」的设计在这里都不成立。

4.（次一级）**全局 a11y 开关是硬前置。** `toolkit-accessibility` 没开的话，VS Code 和 Thunderbird 在 AT-SPI 里就是空壳；开了之后又要承担建树的延迟与内存开销。这个状态需要显式检测而不是假设。

---

# 附录：证据出处索引

| 结论 | 出处 |
| --- | --- |
| 三个 domain 的任务数与不可行名单 | `evaluation_examples/test_all.json`、`evaluation_examples/test_infeasible.json` |
| 终端输出靠 AT-SPI 读取，且要求窗口 active + terminal focused | `desktop_env/server/main.py` → `_has_active_terminal()` / `get_terminal_output()` |
| a11y 树包含 `act:` 动作名、描述、快捷键 | `desktop_env/server/main.py` → `_create_atspi_node()` 里 `node.queryAction()` 分支 |
| 树的深度/宽度截断 | `desktop_env/server/main.py:411-412`（`MAX_DEPTH = 50`、`MAX_WIDTH = 1024`） |
| Thunderbird 文本含 `￼` 需清洗 | `desktop_env/server/main.py` → `queryText()` 之后的 replace |
| Gecko/Chromium 需要先开 `toolkit-accessibility` | `desktop_env/server/README.md`「About the Converted Accessibility Tree」 |
| VS Code 状态靠自研扩展而非 a11y 提取 | `desktop_env/server/README.md`「VS Code plugin installation」+ `desktop_env/evaluators/getters/vscode.py` |
| CSS 选择器 / xpath 判分的实现 | `desktop_env/evaluators/metrics/general.py` → `check_accessibility_tree()` |
| agent 侧丢弃 `st:showing != true` 的节点 | `mm_agents/accessibility_tree_wrap/heuristic_retrieve.py` → `judge_node()` |
| Thunderbird 撰写窗口附件区可寻址 + 官方脚本回落坐标点击 | `d38192b0` 的 postconfig 下载的 `show-thunderbird-attachments.py`（HuggingFace `xlangai/ubuntu_osworld_file_cache`，不在仓库内） |
| VM 基线版本（Ubuntu / VS Code 1.91.1） | `desktop_env/server/README.md`「Software Installation Source」 |

## 三个 domain 的任务 ID 全表

**os**：`13584542-872b-42d8-b299-866967b5c3ef`、`23393935-50c7-4a86-aeea-2b78fd089c5c`、`28cc3b7e-b194-4bc9-8353-d04c0f4d56d2`、`37887e8c-da15-4192-923c-08fa390a176d`、`3ce045a0-877b-42aa-8d2c-b4a863336ab8`、`4127319a-8b79-4410-b58a-7a151e15f3d7`、`4783cc41-c03c-4e1b-89b4-50658f642bd5`、`4d117223-a354-47fb-8b45-62ab1390a95f`、`5812b315-e7bd-4265-b51f-863c02174c28`、`5c1075ca-bb34-46a3-a7a0-029bd7463e79`、`5ced85fc-fa1a-4217-95fd-0fb530545ce2`、`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`、`6f56bf42-85b8-4fbb-8e06-6c44960184ba`、`94d95f96-9699-4208-98ba-3c3119edf9c2`、`a462a795-fdc7-4b23-b689-e8b6df786b78`、`a4d98375-215b-4a4d-aee9-3d4370fccc41`、`b3d4a89c-53f2-4d6b-8b6a-541fb5d205fa`、`b6781586-6346-41cd-935a-a6b1487918fc`、`bedcedc4-4d72-425e-ad62-21960b11fe0d`、`c288e301-e626-4b98-a1ab-159dcb162af5`、`e0df059f-28a6-4169-924f-b9623e7184cc`、`ec4e3f68-9ea4-4c18-a5c9-69f89d1178b3`、`f9be0997-4b7c-45c5-b05c-4612b44a6118`、`fe41f596-a71b-4c2f-9b2f-9dcd40b568c3`

**vs_code**：`0512bb38-d531-4acf-9e7e-0add90816068`、`0ed39f63-6049-43d4-ba4d-5fa2fe04a951`、`276cc624-87ea-4f08-ab93-f770e3790175`、`4e60007a-f5be-4bfc-9723-c39affa0a6d3`、`53ad5833-3455-407b-bbc6-45b4c79ab8fb`、`57242fad-77ca-454f-b71b-f187181a9f23`、`5e2d93d8-8ad0-4435-b150-1692aacaa994`、`6ed0a554-cbee-4b44-84ea-fd6c042f4fe1`、`70745df8-f2f5-42bd-8074-fbc10334fcc5`、`7aeae0e2-70ee-4705-821d-1bba5d5b2ddd`、`7c4cc09e-7a92-40dd-8338-b2286535c4ed`、`847a96b6-df94-4927-97e6-8cc9ea66ced7`、`930fdb3b-11a8-46fe-9bac-577332e2640e`、`9439a27b-18ae-42d8-9778-5f68f891805e`、`971cbb5b-3cbf-4ff7-9e24-b5c84fcebfa6`、`982d12a5-beab-424f-8d38-d2a48429e511`、`9d425400-e9b2-4424-9a4b-d4c7abac4140`、`c6bf789c-ba3a-4209-971d-b63abf0ab733`、`dcbe20e8-647f-4f1d-8696-f1c5bbb570e3`、`e2b5e914-ffe1-44d2-8e92-58f8c5d92bb2`、`ea98c5d7-3cf9-4f9b-8ad3-366b58e0fcae`、`eabc805a-bfcf-4460-b250-ac92135819f6`、`ec71221e-ac43-46f9-89b8-ee7d80f7e1c5`

**thunderbird**：`08c73485-7c6d-4681-999d-919f5c32dcfa`、`10a730d5-d414-4b40-b479-684bed1ae522`、`15c3b339-88f7-4a86-ab16-e71c58dcb01e`、`3f28fe4f-5d9d-4994-a456-efd78cfae1a3`、`3f49d2cc-f400-4e7d-90cc-9b18e401cc31`、`5203d847-2572-4150-912a-03f062254390`、`7b1e1ff9-bb85-49be-b01d-d6424be18cd0`、`9b7bc335-06b5-4cd3-9119-1a649c478509`、`9bc3cc16-074a-45ac-9bdc-b2a362e1daf3`、`a10b69e1-6034-4a2b-93e1-571d45194f75`、`a1af9f1c-50d5-4bc3-a51e-4d9b425ff638`、`d38192b0-17dc-4e1d-99c3-786d0117de77`、`dd84e895-72fd-4023-a336-97689ded257c`、`dfac9ee8-9bc4-4cdc-b465-4a4bfcd2f397`、`f201fbc3-44e6-46fc-bcaa-432f9815454c`
