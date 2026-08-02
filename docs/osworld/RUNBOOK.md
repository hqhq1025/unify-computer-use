# OSWorld 跑测手册：环境部署与测试

这份文档写给**要在另一台机器上把这套跑测复现出来的人**，包括并行全量跑。

写得啰嗦是有意的。这套东西踩过的坑里，有一多半不是"报错了不知道怎么修"，
而是**它默默地跑通了、但跑的不是你以为的那件事**——环境少布置了一步、
判据没跑到、Chrome 连的是另一个端口。那种错不会报警，只会让你拿到一批
看起来很正常的假数据。所以每一节都会写清楚"漏了会怎样"。

---

## 0. 先讲结论：能不能并行

**能，但只能按"一台机器（或一个容器/虚拟机）一个桌面"去并行，不能在同一个桌面上跑多个 worker。**

原因是硬的，不是保守：

| 共享的东西 | 后果 |
|---|---|
| **X11 输入合成** | AT-SPI 的 `generate_keyboard_event` / `generate_mouse_event` 走 XTEST，**全局投递**——它落在当前获得焦点的窗口上，而不是调用方指定的那个应用。两个 worker 同时打字，字符会互相穿插到对方的窗口里。 |
| **AT-SPI 总线** | `list_apps` / `get_app_state` 看到的是整个桌面上的所有应用。worker A 的 GIMP 会出现在 worker B 的候选表里，`resolve_app("gimp")` 可能解析到别人的进程。 |
| **`pkill -x`** | 环境清理会 `pkill -x chrome` / `pkill -x gimp`，**杀掉的是全机器的**，包括别的 worker 正在用的那个。 |
| **Chrome 用户配置目录** | `~/.config/google-chrome/Default` 只有一份，会话文件、Preferences、历史记录全都共用。 |
| **Chrome 调试端口** | 默认 1337，一台机器只能有一个 Chrome 监听它。 |

所以并行的正确形态是**横向复制整台机器**：N 个容器/虚拟机，每个里面一个完整桌面，
各跑一个 worker，跑完把各自的 `results.jsonl` 合并。

如果你一定要在**一台物理机**上并行，那么每个 worker 至少要有：
独立的 `HOME`、独立的 `DISPLAY`（各自一个 Xvfb）、独立的 D-Bus 会话总线、
独立的 `CHROME_CDP_PORT`。这等价于手工搭了个轻量容器，不如直接用容器。

还有一条**资源**上的硬限制，见 §7：官方判据里有几道要对 2000 万像素的图算结构
相似度，单进程瞬时要好几 GB 内存。并行度不要超过 `可用内存 / 6GB`。

---

## 1. 前置条件

### 1.1 机器

- Linux + **X11 桌面会话**（GNOME 即可）。不能是纯无头机——需要真的有窗口管理器。
  Wayland 下 XTEST 和窗口截图都会退化，本仓库的测试全部在 X11 下做的。
- 内存 **≥ 8 GB**（本机 3.8 GB + 3.1 GB swap，第 57 题的官方判据当场把进程 OOM 掉了，见 §7）。
- 磁盘 **≥ 40 GB 可用**。题目素材要从 HuggingFace 下载，轨迹会持续累积；
  本机一度跑到 98% 满，GIMP 自己弹了 "Low Disk Space" 通知。
- 4 核以上。

### 1.2 无障碍总线必须开着

```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
# 验证：应当输出 true
gsettings get org.gnome.desktop.interface toolkit-accessibility
# GTK 应用还需要这个环境变量，通常桌面会话已经设好
echo "$GTK_MODULES"     # 期望包含 gail:atk-bridge
```

**漏了会怎样**：不会报错。`list_apps` 只会返回空的或极少的应用，
每道题都失败，看上去像"模型什么都做不了"。

快速自检：

```bash
scripts/a11y-readiness-probe.py
```

### 1.3 依赖

```bash
# Python：跑测垫片与官方判据
python3 -m pip install pillow numpy scikit-image opencv-python \
    pypdf python-docx openpyxl python-pptx beautifulsoup4 lxml \
    requests pyautogui mutagen odfpy rapidfuzz

# 系统：桌面控制与探针
sudo apt-get install -y xdotool wmctrl x11-utils python3-gi \
    gir1.2-atspi-2.0 gir1.2-gtk-3.0

# Go：构建 MCP 服务器（1.22+）
go version
```

被测应用按题目分组安装：`google-chrome`、`libreoffice`、`gimp`、`vlc`、
`thunderbird`、`code`(VS Code)、`nautilus`、`gedit`、`file-roller`。

---

## 2. 一次性搭建

### 2.1 拉 OSWorld，并**记下 commit**

```bash
git clone https://github.com/xlang-ai/OSWorld.git ~/OSWorld
cd ~/OSWorld && git log -1 --format='%h %ad %s' --date=short
```

本仓库当前记录的数据全部基于 `091f5ef`（2026-07-28）。

**判据一行都不许改。** 自己改官方判据等于自己给自己出考卷，测出来的数没有任何
可比性。随时可以这样自查：

```bash
cd ~/OSWorld && git status --porcelain && git diff --stat origin/main
# 两条命令都应当没有输出
```

如果你换了 commit，请在结果里注明——判据修过的题，跨版本的分数不能直接比。

### 2.2 构建 MCP 服务器

```bash
cd ~/unify-computer-use
scripts/build-open-computer-use-linux.sh
ls -la dist/linux/amd64/open-computer-use     # 跑测默认用这个路径
```

改过 `apps/OpenComputerUseLinux/` 里的任何东西之后都要重新构建，
**否则你测的是上一版**。Python 运行时是 `go:embed` 进二进制的，
改 `runtime.py` 同样必须重新构建。

### 2.3 让 `claude` CLI 可用

跑测通过 `claude -p` 驱动真实的 Claude Code，MCP 由脚本自动注册
（`claude mcp add ocu -- <binary> mcp`），你不需要手工注册。
但 `claude` 必须在 PATH 里、且已登录。

### 2.4 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OSWORLD_ROOT` | `/home/user/OSWorld` | OSWorld 仓库路径 |
| `OSWORLD_CACHE` | `/tmp/osworld-cache` | 题目素材下载缓存 |
| `OSWORLD_RESULTS` | `docs/osworld/results.jsonl` | 结果文件。**并行时每个 worker 必须给不同的值**，见 §6 |
| `OSWORLD_WORKDIR` | `/tmp/ocu-agent-run` | agent 的工作目录。**并行时必须不同** |
| `CHROME_CDP_PORT` | `1337` | Chrome 远程调试端口 |
| `PYTHONPATH` | — | 必须包含 `scripts/osworld-stubs` |

`PYTHONPATH` 那条不是可选的：

```bash
export PYTHONPATH=~/unify-computer-use/scripts/osworld-stubs
```

**漏了会怎样**：`import desktop_env` 失败，或者 import 到官方那套需要虚拟机的实现，
判分直接抛异常。

---

## 3. 五个命令

```bash
cd ~/unify-computer-use
export PYTHONPATH=scripts/osworld-stubs
```

| 命令 | 作用 |
|---|---|
| `python3 scripts/osworld-bench.py list [--limit N]` | 按官方 `test_all.json` 的顺序列出全部 369 题 |
| `python3 scripts/osworld-bench.py deploy <题号或id>` | 布置这道题的初始环境 |
| `python3 scripts/osworld-bench.py agent <题号> --budget N --attempt K --note "…"` | 让 cc 挂着这个 MCP 做一遍，做完自动判分并记录 |
| `python3 scripts/osworld-bench.py score <题号>` | 单独判分（我手工做完之后用） |
| `python3 scripts/osworld-bench.py record <题号> --who me\|cc --attempt K --score S --note "…"` | 手工补一条记录 |

题号是 **1-based**，`deploy 42` 是第 42 题。也可以传任务 id 的前缀。

### 3.1 一道题的完整流程

```bash
export PYTHONPATH=scripts/osworld-stubs

# 1) 布置环境
python3 scripts/osworld-bench.py deploy 42
#    注意看有没有 "⚠️ 以下 config 步骤没有执行"——有的话这道题环境不完整，
#    任何失败都不能算模型的账，见 §5

# 2) 等桌面稳定（Chrome 起来、页面加载完）
sleep 12

# 3) 让 cc 做。**--skip-config 是必须的**，因为上面已经 deploy 过了
python3 scripts/osworld-bench.py agent 42 --skip-config --budget 6 --attempt 1 --note "cc 第一次"
#    输出：步数 / 观测 token / 用时 / 得分 / 判据明细
#    结果自动追加进 results.jsonl，轨迹自动归档
```

**`--skip-config` 漏了会怎样**（实测，第 117 题）：`agent` 默认会把 config
再跑一遍。对大多数题这是幂等的，看不出问题；但对 LibreOffice 是致命的——
deploy 已经把文件复制好并用 soffice 打开了，agent 又**覆盖了同一个文件**，
于是 LibreOffice 弹出 "Document Has Been Changed by Others"。

这个对话框不会打断 agent（它在另一个窗口里），却会**挡住判分时的 Ctrl+S**，
于是 agent 做对的改动根本没落盘，判据读到的还是原始文件。第 117 题就是这样：
cc 正确地把缩放从 260% 改成 100%，而判据读出来的仍是 260。

排查这类问题时先看一眼 `wmctrl -l`：桌面上多出来的对话框往往就是答案。

不过就重来，**同一题最多三次**，三次不过转下一题。

### 3.2 后台跑一道题的标准写法

```bash
nohup env PYTHONPATH=scripts/osworld-stubs timeout 1500 bash -c \
  'python3 scripts/osworld-bench.py deploy 42 >/dev/null 2>&1; sleep 12; \
   python3 scripts/osworld-bench.py agent 42 --budget 6 --attempt 1 --note "cc 第一次"' \
  > /tmp/t42.log 2>&1 &
```

`timeout 1500` 是必要的：个别题会跑很久（实测最长 1002 秒 / 66 步）。

---

## 4. cc 是怎么被调用的（以及为什么这样调用）

```
claude -p "<题目原文>"
  --append-system-prompt "You are an agent which follow my instruction
                          and perform desktop computer tasks as instructed."
  --permission-mode bypassPermissions
  --output-format stream-json --verbose
  --max-budget-usd <budget>
  --disallowedTools Read Write Edit NotebookEdit Glob Grep
                    WebFetch WebSearch Task TodoWrite KillShell BashOutput
```

**① 系统提示逐字照抄 OSWorld 官方，一个字都不多加。**
官方 `mm_agents/prompts.py` 第一句就是那句话。我们最初只喂原始指令，
结果第 17 题的指令是疑问句，cc 把它**当问题回答了**——给了一份正确的操作建议，
一步没动手。它没做错，是我们没告诉它该动手。
后来我一度又补了一句 "Actually operate the computer…"，**已收回**：
那超出了官方措辞，等于给自己的实现开小灶，测出来的数就不能和别人比了。

**② Bash 是开的，其余内置工具禁用。**
Bash 原来也在禁用列表里，理由是"不禁的话 agent 会绕开 GUI 直接改文件"。
这个理由站得住，但它测的也不是真实的 Claude Code——真实用户的 Bash 一直开着。
而且轨迹证明禁不住：Bash 关闭期间有 2 条轨迹，模型自己通过 GUI 打开 GNOME 终端、
往里 type_text 打 Python 代码，把终端当成一个图形应用来用。

跑纯链路口径时加 `--no-bash`。

**③ 工作目录是空的临时目录。**
避免它读到仓库里的任何东西。

### 4.1 开不开 Bash，测的是两件不同的事

实测（截至第 134 题）：

| 阶段 | 轨迹 | 总步数 | Bash | MCP |
|---|---|---|---|---|
| 第 1–69 题（Bash 关） | 66 | 997 | 0（0%） | 997（**100%**） |
| 第 70–107 题（Bash 开） | 38 | 764 | 503（**66%**） | 261（34%） |
| 第 108 题起（Bash 开） | 27 | 416 | 198（48%） | 218（52%） |

Bash 一开，`click` 从 380 次崩到 45 次。典型做法是写脚本绕开桌面——
第 70 题用 `gimp-2.10 -n -i -d --batch-interpreter=plug-in-script-fu-eval`
跑 headless 批处理，第 76 题写 LibreOffice Basic 宏 + xdotool 驱动 Basic IDE。

**所以两种口径的通过率不能合成一个数字报。** `osworld-report.py` 会按
`results.jsonl` 里的 `bash` 字段分开统计。

第三行的 Bash 占比回落到 48%，与"修好陈旧二进制"同时发生——但**题目也换了**
（70–107 是 os + calc，表格题天生适合 shell；108 之后混进 impress，
幻灯片格式操作 shell 不好写）。两个变量一起变了，分不开，
所以这里只陈述数字，不下因果结论。

### 4.2 绕开 GUI 会产生一类新的失败

第 119 题给出的对照最干净：

  前两次 cc 用 Bash + openpyxl 直接写 xlsx → 文件里只有 6 行带显式字体色
        → 官方 compare_table 逐格比字体色，碰到一边 None 一边是对象，
          抛 `AttributeError: 'NoneType' object has no attribute 'rgb'`
  第三次 cc 改用 UNO 让 LibreOffice 自己写 → 29 行全有显式字体色 → 1.0

三次的语义都对。差别只在于**文件是谁写的**：LibreOffice 给每个单元格写显式样式，
openpyxl 不写，而官方判据假定的是前者。

也就是说开 Bash 之后既有"通过率虚高"（绕开 GUI 做完），
也有"通过率虚低"（正确答案因文件结构不对而拿 0）。

---

## 5. 判分：什么算失败，什么不算

判分**一律调用 OSWorld 官方评估器**，我们不自己写判据。

### 5.1 三类"不是模型失败"的情况

这套跑测里最该避免的污染，是把环境缺陷、仪器缺陷记成模型失败——
它会让人去修一个并不存在的产品问题。目前会被显式区分出来的有三类：

**(a) 环境布置不全。** `deploy` 会打印 `⚠️ 以下 config 步骤没有执行: …`。
出现这行就说明这道题的初始状态和题面对不上，结果不作数。

**(b) 判据跑不了。** 判分在**子进程**里跑。子进程被内核 OOM 杀掉时，
记的是 `评估器被内核 OOM 杀掉…环境不支持判分，不计为模型失败`，
分数记 `None` 而不是 0。
（出处：第 57 题的官方判据要对两张 5184×3888 的图算结构相似度，
float64 中间数组一个就 1.6 GB。不隔离的话它会把跑测进程一起带走，
那一题既没有分数也没有记录，从外面看像"卡死了 25 分钟"。）

**(c) `infeasible` 题。** 官方判据是空函数，靠 agent 输出 `FAIL` 判分。
我们的 agent 说人话，所以判据落在"它有没有如实说做不到"。
关键词判据天然不精确，**所以整段自述会原样存进 `results.jsonl`**，
读的人可以自己复核并推翻这个判断。

### 5.2 postconfig 必须跑

很多题的判据前面挂着 `postconfig`——比如 `pkill chrome` 强制 Chrome 把
Preferences 落盘、或者用 pyautogui 按 Ctrl+Shift+E 导出图片。
`score` / `agent` 都会自动跑它。

**漏了会怎样**：第 1 题里 Chrome 的默认搜索引擎在界面上明明已经变成
`Microsoft Bing (Default)`，判分却是 0.0——因为 Chrome 惰性刷盘。
一次真实的成功被记成失败，然后你会去修一个并不存在的缺陷。

---

## 6. 并行全量跑

### 6.1 推荐形态：一容器一桌面

每个 worker 一个完整桌面，题目按 `list` 的顺序切片分配：

```bash
# worker i（共 N 个），跑第 i, i+N, i+2N … 题
export PYTHONPATH=~/unify-computer-use/scripts/osworld-stubs
export OSWORLD_RESULTS=/data/results-worker-$i.jsonl
export OSWORLD_WORKDIR=/tmp/ocu-agent-run-$i

for n in $(seq $i $N 369); do
  timeout 1500 bash -c "
    python3 scripts/osworld-bench.py deploy $n >/dev/null 2>&1
    sleep 12
    python3 scripts/osworld-bench.py agent $n --budget 8 --attempt 1 --note 'worker-$i'
  " >> /data/log-worker-$i.txt 2>&1
done
```

**切片要交错（i, i+N, i+2N…），不要按段切。** 题目是按应用分组排的
（chrome 一段、gimp 一段、libreoffice 一段…），按段切会让某个 worker
整轮只跑 GIMP，而 GIMP 恰好是每节点最慢的工具包（实测 8.45 ms/节点），
那个 worker 会拖到最后。

跑完合并：

```bash
cat /data/results-worker-*.jsonl > docs/osworld/results.jsonl
python3 scripts/osworld-report.py > docs/osworld/README.md
```

### 6.2 为什么每个 worker 必须有自己的结果文件

单条记录带着整段自述，**经常超过 4 KB**。超过 `PIPE_BUF` 的 `O_APPEND` 写
**不保证原子**，多进程交错写会把两条 JSON 拧成一行，事后谁也解析不出来。
"数据只追加不重写"的前提是每一行都完整。

### 6.3 并行度上限

- **内存**：判据瞬时可能吃几 GB。`并行度 ≤ 可用内存 / 6GB`。
- **磁盘**：素材缓存可以共享（`OSWORLD_CACHE` 指到同一个网络盘即可，只读为主），
  但轨迹是每 worker 各自累积的。
- **网络**：素材从 HuggingFace 下载，N 个 worker 同时冷启动会打满带宽。
  建议先用一个 worker 把 `OSWORLD_CACHE` 预热一遍。

---

## 7. 已知的坑（每条都是实测踩出来的）

### 7.1 `pkill -f` 会杀掉自己

```bash
pkill -f chrome        # ✗ -f 匹配整条命令行，会匹配到**发起这次调用的 shell**
pkill -x chrome        # ✓
```

本仓库栽过三次，表现是脚本莫名以 143/144 退出。

### 7.2 agent 在跑的时候不要读基线

两者驱动的是同一个桌面。实测：并发时基线报 3/5，清场后重测 5/5。
**agent 活着的时候拿到的任何测量值都不作数。**

### 7.3 GIMP 的崩溃恢复框

上一道 gimp 题结束时被 `pkill` 掉，GIMP 会把它当成崩溃，
下一题一开局先弹 "Image Recovery" 模态框，恢复出来的图还是坏的。
`deploy` 现在会自动清 `~/.config/GIMP/2.10/backups/`。
gimp 段有 26 道题，不清就是给后面 25 道全埋雷。

### 7.4 Chrome 的会话残留

`clean_chrome_session` 除了删 `Current Session` / `Current Tabs` /
`Last Session` / `Last Tabs`，**还必须删 `Sessions/` 整个目录**——
实测只删前四个文件之后按 Ctrl+Shift+T，Chrome 从 `Sessions/` 里
捞回了上一道题的整个窗口。

### 7.5 Chrome 调试端口只有一个来源

`CHROME_CDP_PORT`（默认 1337）同时用于启动参数和 CDP 客户端。
**历史上这里是两个数**：启动用 1337、客户端写死连 9222，中间靠一条手工起的
`socat tcp-listen:9222,fork tcp:localhost:1337` 桥着。那条 socat 不在任何脚本里，
换台机器就没有——而缺了它不会报错，只是 `chrome_open_tabs` / `chrome_close_tabs` /
取当前标签页**全部静默失效**。已经统一成一个常量，这里记一笔是因为
如果你看到老的部署脚本里有 socat，那是残留，可以删掉。

### 7.6 评估器的内存

见 §5.1(b)。判分已经隔离到子进程，但**并行时要按内存算并行度**。

---

## 8. 轨迹

每次 `agent` 跑完，轨迹会自动做两件事：

1. 完整的 stream-json 留在 `/tmp/osworld-agent-<id8>.jsonl`（含 base64 截图，很大）
2. 自动归档到 `docs/osworld/traces/osworld-agent-<id8>.jsonl.gz`，
   **剥掉 base64 截图**再 gzip

剥图的理由：分析只需要结构，不需要像素。实测 26 条原始轨迹含图 172 MB，
剥图后 16.7 MB，压缩后 2.1 MB——可以直接进版本库。

分析工具：

```bash
scripts/analyze-agent-traces.py                          # 默认扫 /tmp 里的
scripts/analyze-agent-traces.py docs/osworld/traces/*.gz  # 扫归档的
```

它统计：工具调用占比、报错最多的工具、最常见的错误原文、重复调用、
语义通道退让、最常见的相邻工具对。**只统计，不下结论**——
这个脚本的第一版指标是错的（把正常的坐标点击算成了"退让"），
教训写在脚本注释里：一个把正常行为算成病症的指标，比没有指标更糟。

---

## 9. 出结果

```bash
python3 scripts/osworld-report.py > docs/osworld/README.md
```

`results.jsonl` **只追加、不重写**；这份报告随时可以从它重新生成。
叙述可以推翻重写，数据不许。
