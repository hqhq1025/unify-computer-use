# OSWorld GIMP / VLC 任务的 GUI 操作清单

状态：reference ｜ 撰写于 2026-07-30

## 数据来源

| 来源 | 路径 | 用途 |
|---|---|---|
| 任务定义 | `/home/user/OSWorld/evaluation_examples/examples/gimp/`（26 个 json） | `instruction` / `config` / `evaluator` |
| 任务定义 | `/home/user/OSWorld/evaluation_examples/examples/vlc/`（17 个 json） | 同上 |
| 判分实现 | `/home/user/OSWorld/desktop_env/evaluators/metrics/gimp.py` | 确认"做到什么程度算过" |
| 判分实现 | `/home/user/OSWorld/desktop_env/evaluators/metrics/vlc.py` | 同上 |
| a11y 实测 | 本机 2026-07-30，Ubuntu 22.04 + X11 GNOME + at-spi2-core 2.44 | 判断可寻址性 |
| 素材实测 | 从 HuggingFace 下载任务用的 `.xcf`，用 `gimp-console` 读图层结构 | 确认画布依赖 |

实测环境的应用版本：**GIMP 2.10.30（AT-SPI toolkit 上报 `GAIL 2.24.33`，即 GTK+2）**、
**VLC 3.0.16（AT-SPI toolkit 上报 `Qt 5.15.3`）**——与 OSWorld 镜像里的版本一致
（任务 `a746add2` 的判分路径是 `~/.config/GIMP/2.10/action-history`，佐证 GIMP 主版本为 2.10）。

## 统计口径

1. **分母**：先剔除 `evaluator.func == "infeasible"` 的任务，它们要求 agent 识别"做不到"并拒绝，
   不产生实际 GUI 操作。GIMP **9/26 infeasible**，剩 **17** 个；VLC **2/17 infeasible**，剩 **15** 个。
2. **"操作"的定义**：UI 层面的动作**类型**，不是动作次数。一个任务可以同时计入多类。
3. **归类依据**：按我判断的「最短合理 GUI 路径」归类。这一步不是 json 里编码的信息，
   是人工判断；每一类都给出任务 id，可回溯核对。
4. **a11y 判断**：凡标注「实测」的，都是本机跑过 AT-SPI 调用并用截图/配置文件回读验证过的；
   凡标注「未实测」的，明确写出来，不做推断。
5. **不统计**：`config` 里 OSWorld 自己用 pyautogui 做的初始化点击（每个 VLC 任务都有一次
   屏幕中心点击用来把窗口拉到前台），那是 harness 行为不是 agent 行为。

### 一个容易漏掉的口径：GIMP 的导出是 harness 代做的

10 个 GIMP 任务的 `evaluator.postconfig` 里，OSWorld 用 pyautogui 自己敲了
`Shift+Ctrl+E` → 输入文件名 → `Enter` → `Enter` 来完成导出：
`06ca5602`、`2a729ded`、`554785e9`、`72f83cdc`、`734d6579`、`7a4deb26`、`d16c99dc`、
`e2dd0213`、`f4aec372`、`f723c744`。

**含义有两层**：
- agent **不需要**自己导出，只要把编辑做完；
- 但 agent **必须把编辑做在那个还开着的 GIMP 窗口里**——harness 的 `Shift+Ctrl+E` 导出的是
  当前活动图像。走 `gimp-console` 之类的进程外脚本旁路会导出未编辑的原图，直接判 0。

同理，4 个 GIMP 配置类任务的 postconfig 会替 agent 按 `Ctrl+Q`（`7767eef2`、`7b7617bd`、
`b148e375`、`d52d6308`），因为 `gimprc` / `sessionrc` 是退出时才落盘的；
6 个 VLC 配置类任务的 postconfig 会 `pkill vlc` 再重启（`215dfd39`、`386dbd0e`、`9195653c`、
`a5bbbcd5`、`d06f0d4d`、`f3977615`），同理。

---

## 一、GIMP：必须的操作（按出现频次降序）

分母 = 17 个可行任务。

### G1. 菜单导航（菜单栏 → 多级子菜单 → 命令项）— **17/17**

- **典型形态**：从菜单栏进入某个多级路径触发命令，例如
  `a746add2` 的 `Filters > Light and Shadow > Vignette...`、
  `06ca5602` 的 `Image > Mode > Indexed...`、
  `72f83cdc` 的 `Image > Transform > Flip Horizontally`。
- **a11y 判断：完全可寻址，而且是 GIMP 最强的一面。**
  实测 GIMP 主窗口的 AT-SPI 树共 3196 节点，其中 `menu item` 723 个、`menu` 81 个、
  `check menu item` 67 个——**整棵菜单树在菜单没有被展开时就已经全量挂在树上**。
  更关键的是：对深层的 `menu item "Vignette..."` 节点**直接调 `click` action 就打开了
  Vignette 对话框**，不需要先逐级 ShowMenu，全程零坐标。
  代价是这些未展开的菜单项 extents 是 `@-2147483648,-2147483648 1x1`（GTK 的
  "未分配" 哨兵值），坐标回退路径对它们无效——只能走语义调用。

### G2. 模态对话框参数交互（填数值/选项 + 确认）— **12/17**

- 任务：`06ca5602`（Indexed 对话框）、`554785e9`（Hue-Saturation）、`734d6579`（前景色选择器）、
  `7767eef2`（Preferences > Interface > Theme）、`77b8ab4d`（Export Image）、
  `7a4deb26` / `f723c744`（Brightness-Contrast）、`7b7617bd`（Preferences > System Resources）、
  `b148e375`（New Layer）、`d16c99dc`（Scale Layer）、`dbbf4b99`（RAW 载入 + Export Image）、
  `2a729ded`（Add Alpha Channel / Color to Alpha）。
  另有 `a746add2` 只需要把 Vignette 对话框**打开**、不需要改参数，单独计。
- **典型形态**：菜单触发一个模态对话框，在里面改一两个数值/下拉项，再点 OK/Export。
- **a11y 判断：对话框框架可寻址，参数控件多数不可寻址。**
  - 好的一半：对话框以 `dialog` 角色出现在树里（实测 `dialog "Vignette"`、
    `dialog "Brightness-Contrast"`），底部按钮 `push button "OK" / "Cancel" / "Reset" / "Help"`
    都有名字、有 `click` action。
  - **坏的一半：GEGL 系调色/滤镜对话框里的参数控件 `name` 是空字符串。**
    实测 Brightness-Contrast 对话框里就是两个
    `spin button "" ... val=0.0[-127.0..127.0]`——**从 a11y 树上无法区分哪个是亮度、
    哪个是对比度**，只能靠"GIMP 里亮度在上、对比度在下"这种顺序先验，或者看一眼截图。
    Vignette 对话框更极端，8 个匿名 spin button（radius / softness / gamma / proportion /
    squeeze / center-x / center-y / rotation）。
    参数名是画在 GimpSpinScale 控件内部的，没有 ATK label 关联。
  - 好在这些 spin button 都是 `EDITABLE` 且带 Value 接口，**一旦知道是哪个就能设值**。

### G3. 图层面板操作（选中图层 / 新建 / 命名）— **5/17**

- 任务：`734d6579`（选中 Background 层再填充）、`b148e375`（新建图层并命名 Square）、
  `d16c99dc`（选中 dog 层再缩放）、`e2dd0213`（选中文本层再移动）、`f4aec372`（选中三角形所在层）。
- **典型形态**：在右下角 Layers dock 里点中某一层，让后续命令作用在它身上。
- **a11y 判断：可寻址，但只有走对接口才生效——这是一个已实测的静默失败点。**
  - 图层名字**是**暴露的：实测 `tree table` 下有 `table cell "Background"`、
    `table cell "user-add.png"`，还带 `edit` / `activate` 两个 action。
  - **对 cell 调 `activate` 返回 `True`，但活动图层根本没变**（截图前后比对，
    Layers dock 区域 0 像素差异）。同一位置用 xdotool 真点，活动图层立刻切换（15 万像素差异）。
  - **正确路径是父节点 `tree table` 的 ATK Table 接口 `add_row_selection(row)`**，
    实测调用后 Layers dock 的高亮切到目标行。GIMP 的 tree table 接口齐全：
    `Accessible, Collection, Component, Selection, Table`。
  - 落地含义：runtime 里 `click(element)` 若只映射到 action 接口，这 5 个任务会全部静默失败。

### G4. 工具选择（选择工具/画笔/移动工具）— **3/17**

- 任务：`2a729ded`（模糊选择或自由选择）、`e2dd0213`（移动工具）、`f4aec372`（移动/对齐工具）。
- **典型形态**：切到某个工具，再去画布上操作。
- **a11y 判断：工具箱本身完全不可寻址，但有等价的菜单路径可以绕过。**
  - 实测左侧 dock 里 26 个可见 `toggle button` 的 **`name` 和 `description` 全部是空字符串**，
    只有 extents（`@70,124 47x40`、`@117,124 47x40` …）。纯 a11y 无法知道哪个是哪个工具。
  - 但 `Tools > Selection Tools > Fuzzy Select` / `Tools > Transform Tools > Move` 这些菜单项
    **有完整名字**（实测树中可见），走 G1 的直接 click 即可。
  - 所以这条不是死路，只是"点工具箱图标"这个人类直觉动作在 a11y 下必须改写成菜单路径。

### G5. 画布直接操作（选区 / 拖拽 / 像素级定位）— **3/17**

- 任务：`2a729ded`、`e2dd0213`、`f4aec372`。详见下面的专章。
- **a11y 判断：彻底不可寻址，无任何回退。**
  实测 GIMP 画布在 AT-SPI 里是一个 **`panel "" @287,149 1402x880`，零子节点、零 action**。
  图像内容、选区、图层在画布上的位置，在无障碍层面**完全不存在**——只是一个矩形。

### G6. 应用配置类（Preferences / Windows 菜单开关）— **3/17**

- 任务：`7767eef2`（主题改 Light）、`7b7617bd`（undo-levels = 100）、`d52d6308`（隐藏左侧 dock）。
- **典型形态**：`Edit > Preferences` 里改一项；`d52d6308` 其实走 `Windows > Hide Docks`（等价 Tab 键），
  写进 `sessionrc` 的 `hide-docks yes`。
- **a11y 判断：可寻址（Preferences 是标准 GTK 对话框 + 左侧分类树）。**
  注意分类树的选择大概率踩 G3 同一个坑（未单独实测 Preferences 的树）。

### G7. 文件导出 / 另存（Export As 文件选择器）— **2/17**

- 任务：`77b8ab4d`（导出成 Desktop/export.jpg）、`dbbf4b99`（RAW → Desktop/yicun.jpg）。
- **只有这 2 个需要 agent 自己导出**，其余 10 个由 harness 代做（见前面口径说明）。
- **a11y 判断：GTK 文件选择器的文件名输入框和 Export 按钮通常有名字，可寻址；本次未单独实测。**

### G8. 颜色选择（前景色 / HTML notation）— **1/17**

- 任务：`734d6579`（背景层填绿）。
- **a11y 判断：GIMP 的颜色对话框有 "HTML notation" 文本框（命名控件），可寻址；本次未实测。**

### 附：一条被低估的 a11y 旁路

实测 GIMP 菜单树里存在 `menu "Script-Fu"` → `menu item "Console"`（`Filters > Script-Fu > Console`），
**有名字、有 `click` action**。Script-Fu 控制台作用在**当前打开的图像**上，
因此不会像进程外的 `gimp-console` 那样破坏 harness 的导出 postconfig。
这条路可以一次性绕开 G2 的匿名参数控件、G3 的图层选择、以及 G5 里所有**几何可计算**的位移
（例如 `f4aec372` 的居中）。控制台本身的输入框是否 a11y 可写**未实测**，
但如果可写，它对 GIMP 这个 domain 的价值远超任何裁剪优化。

---

## 二、GIMP：必须看画布内容才能做的任务

这是本次调研里最硬的结论：**GIMP 画布在 AT-SPI 里是一个空 panel**，
所以凡是目标由"图像里的像素在哪"定义的任务，无障碍树天然给不出答案。

逐个列出，并给出实测的图层结构（用 `gimp-console` 读任务自带的 `.xcf`）：

### 强视觉依赖（2 个）

**`2a729ded-3296-423d-aec4-7dd55ed5fbb3` — "把这张图的背景弄成透明"**
- 素材是单层 `dog_with_background.png`，没有现成的图层可用来区分主体和背景。
- 必须在画布上判断"哪些像素是背景"，用模糊选择/自由选择勾出来再删。
- 判分 `check_structure_sim`：与 `dog_cutout_gold.png` 比 SSIM ≥ 0.9，**且尺寸必须完全一致**
  （`img_src.size != img_tgt.size` 直接返回 0）。容错很低。
- a11y 提供的信息量：**零**。

**`f4aec372-4fb0-4df5-a52b-79e0e2a5d6ce` — "选中黄色三角形并把它放到画面正中"**
- 实测 `Triangle_On_The_Side.xcf`：800x800，2 图层：
  - `被粘贴的图层` @ (28,72)，348x203 ← 就是那个黄色三角形
  - `背景` @ (0,0)，800x800
- **图层名是中文的"被粘贴的图层"（= Pasted Layer）**，跟指令里的 "yellow triangle"
  没有任何语义联系。a11y 树只能告诉 agent"有两个图层，一个叫被粘贴的图层"，
  它无法确认哪个是黄色三角形——**必须看画布**。
- 判分 `check_triangle_position`：取出现频次第二的颜色当作三角形色，
  算**质心**，要求落在图像中心 ±5%（±40px）内。注意是**质心不是包围盒中心**——
  三角形的质心在包围盒垂直方向 1/3 处，两者相差约 203/6 ≈ 34px，正好压在 40px 容差线上。
  就算把图层包围盒摆正中也可能不过，这是一个对位置极敏感的判分。
- a11y 提供的信息量：**只有图层名，不含位置**（AT-SPI 不暴露图层偏移量）。

### 中等视觉依赖（1 个）

**`e2dd0213-26db-4349-abe5-d5667bfd725c` — "把文本框挪到左边"**
- 实测 `orange_background.xcf`：2192x1118，2 图层：
  - `Thanks World` @ (976,702)，422x77 ← **图层名有语义，a11y 可寻址**
  - `orange_background.png` @ (0,0)
- 判分 `check_textbox_on_leftside`：最左侧暗像素的 x < 宽度的 35%（< 767px）。**很宽松。**
- 因此存在一条弱视觉路径：用 G3 的 `add_row_selection` 选中 `Thanks World` 层 →
  切移动工具 → 一路按左方向键。但"该往左移多少"仍然需要知道当前偏移 (976,702)，
  而 AT-SPI 不提供这个数——要么看画布，要么大幅超移后靠宽容差兜住。

### 比例

- **强/中视觉依赖：3 / 17 ≈ 17.6%**（占全部 26 个 GIMP 任务的 11.5%）。
- 若把"需要一张截图来消解 G2 匿名 spin button 歧义"也算上，
  再加 `554785e9` / `7a4deb26` / `f723c744`，则 **6 / 17 ≈ 35%**。
  这三个的判分只看方向（饱和度更高 / 亮度更低 / 对比度更高）+ 结构相似，
  给一个固定的非零值就能过，**不需要看图像内容，只需要看一眼控件布局**——
  这一眼理论上可以缓存成先验知识，不必每次调 VLM。

---

## 三、VLC：必须的操作（按出现频次降序）

分母 = 15 个可行任务。

### V1. 菜单导航（菜单栏 → 子菜单项）— **15/15**

- 每个可行任务的起手都是菜单栏（或等价快捷键）：
  `59f21cfb` 的 `Media > Open File...`、`bba3381f` 的 `Media > Open Network Stream...`、
  `8f080098` / `aa4b5023` 的 `Media > Convert / Save...`、
  `fba2c100` / `efcf0d81` 的 `Video > Take Snapshot`、`8d9fd4e2` 的 `Video > Fullscreen`、
  `5ac2891a` 的 `Media > Quit at the end of playlist`、其余 8 个的 `Tools > Preferences`。
- **a11y 判断：完全可寻址，实测有效。**
  Qt 和 GTK 一样**在菜单未展开时就把子项挂在树上**了——首次 dump（没做任何 ShowMenu）
  就能看到 `menu item "Preferences"`。动作模型是 `menu item "Tools"` 的 `ShowMenu`
  加 `menu item "Preferences"` 的 `Press`，实测两步纯语义调用即可打开首选项。
- 一个坑：**Qt 菜单子项的 extents 是相对坐标**（`@0,209 321x23`）而不是屏幕坐标，
  和顶层菜单项的屏幕坐标（`@352,64 38x20`）混在同一棵树里。
  坐标回退路径不能不加区分地直接用 extents。

### V2. 首选项对话框（Tools > Preferences）— **8/15**

按需不需要切到 Advanced 分成两组，因为这两组的 a11y 难度完全不同：

**(a) Simple 面板就够（5 个）**
- `5ac2891a`（play-and-exit=0）、`f3977615`（one-instance=0）、
  `a5bbbcd5`（qt-minimal-view=1，即 "Start in minimal view mode"）、
  `8ba5ae7a`（录制目录改 Desktop，Input/Codecs 页，见任务自带 `source` 链接）、
  `386dbd0e`（全局播放/暂停热键，Hotkeys 页）。
- **a11y 判断：完全可寻址，已端到端验证。**
  实测纯 a11y 走完整条链：`menu item "Tools"`(ShowMenu) → `menu item "Preferences"`(Press)
  → `check box "Use only one instance when started from file manager"`(Toggle)
  → `push button "Save"`(Press)，之后 `~/.config/vlc/vlcrc` 里
  `one-instance-when-started-from-file=0`——**正好就是 `f3977615` 的判分条件，全程零坐标**。
  Simple 面板里的控件命名质量很好：`check box "Allow only one instance"`、
  `radio button "Use native style"`、`combo box "When minimized"` 等都有名字。
  （实测后已把 vlcrc 改回原状。）

**(b) 必须切到 Advanced（"All"）（3 个）**
- `215dfd39`（qt-bgcone=0）、`9195653c`（qt-max-volume=200）、`d06f0d4d`（qt-slider-colours 改黑）——
  三项都在 `Interface > Main interfaces > Qt` 下。
- **a11y 判断：两个必经跳转都静默失败，这 3 个任务必须掺入坐标点击。** 详见第四章。

### V3. 文件路径输入 / 文件选择器 — **6/15**

- `59f21cfb`（选桌面上的 mp4）、`bba3381f`（粘 HLS URL）、`8f080098`（输出 `Desktop/Baby Justin Bieber.mp3`）、
  `aa4b5023`（输出 `Desktop/1984_Apple_Macintosh_Commercial.mp4`）、
  `8ba5ae7a`（Browse 录制目录）、`efcf0d81`（设壁纸时的图片选择）。
- **a11y 判断：未实测。** Qt 的文件对话框在 non-native 模式下是标准 QFileDialog，
  通常有命名控件；但 VLC 在 GNOME 下可能走 portal/GTK 后端，行为不同。**建议单独实测。**

### V4. 播放控制（播放 / 全屏 / 快照）— **5/15**

- `59f21cfb`（判分要求 status.xml 里 `state == playing`）、`bba3381f`（同）、
  `8d9fd4e2`（VLC 窗口尺寸 == 屏幕尺寸）、`fba2c100`（Video > Take Snapshot）、
  `efcf0d81`（同上再设壁纸）。
- **a11y 判断：底部控制条按钮完全不可寻址，但 `Playback` 菜单是可用替代。**
  - 实测底部控制条：9 个 `push button ""` + 3 个 `check box ""`，**name 全为空**。
    只有屏幕 extents 能区分（播放键 `@73,1047 32x32`，上一首/下一首/停止在
    `@117..169,1050 26x26`，全屏/扩展设置/播放列表在 `@207..271`，
    循环/随机两个 checkbox 在 `@297` / `@323`）。
  - 替代路径：`Playback` 菜单下的 `Play` / `Stop` / `Previous` / `Next` / `Record`
    **都有名字**，可以纯语义触发。全屏也有 `Video > Fullscreen`。
  - 所以"点播放键"这个动作和 GIMP 的工具箱同构：**图标按钮无名，但菜单里有等价命名项**。

### V5. 转码 / 串流对话框（Convert / Save）— **2/15**

- `8f080098`（mp4 抽 mp3）、`aa4b5023`（先转正视频再重编码另存）。
- **典型形态**：VLC 里步骤最长的一条链——`Media > Convert/Save` → Add 文件 → Convert/Save →
  选 Profile（`aa4b5023` 还要先在 Profile 里改 Video codec 的 filter）→ 指定输出路径 → Start。
- **a11y 判断：未实测。** 这是两个任务里最可能翻车的地方，建议优先补测。

### V6. 视频效果对话框（Tools > Effects and Filters）— **1/15**

- `aa4b5023`：`Video Effects > Geometry > Transform` 里做 180° 旋转/翻转。
  注意"预览里转正"和"转码时把旋转烧进文件"是两件事，判分 `compare_videos`
  比的是产出文件的逐帧感知哈希，所以旋转必须落进编码。
- **a11y 判断：未实测。**

### V7. 进度条 / 音量条拖动 — **0/15（明确的负结果）**

- 两个需要特定帧的任务（`fba2c100`、`efcf0d81`）中，OSWorld 的 `config` 已经用
  `--start-time=120.5 --stop-time=121 --play-and-pause` 把播放头停在目标帧上了，
  **agent 不需要自己拖进度条**。
- 顺带记录实测结果：两个 `slider ""` 都**无 name**，但都带 Value 接口
  （seek `val=7502.0[0.0..10000.0]`、volume `val=0.0[0.0..125.0]`）和 Increase/Decrease action。
  几何上能区分（seek 是横贯底部的 1749x18，volume 是右下角的 85x26）。
  **能操作，但识别只能靠几何先验。**

### V8. 播放列表操作 — **0/15（负结果）**

17 个 VLC 任务里**没有一个**需要真正操作播放列表（增删、排序、保存）。
`5ac2891a` 的 `Quit at the end of playlist` 只是 Media 菜单里的一个开关。

---

## 四、Qt（VLC）与 GTK（GIMP）在 Linux AT-SPI 上的差异

两者都能拿到树，节点规模也不是问题（本机实测 GIMP 3196 节点、VLC 267 节点）。
真正的差异在**动作层可不可信**。以下每一条都有实测支撑。

| 维度 | GTK+2 / GAIL 2.24.33（GIMP 2.10.30） | Qt 5.15.3（VLC 3.0.16） |
|---|---|---|
| toolkit 上报 | `GAIL 2.24.33` | `Qt 5.15.3` |
| 菜单是否需展开才可见 | 否，全量静态暴露 | 否，全量静态暴露 |
| 深层菜单项可否直接触发 | **可以**，直接 `click` 深层 item 即打开对话框 | 需 `ShowMenu` 父项 + `Press` 子项（两步都实测有效）|
| 菜单项 extents | 未展开时为 `-2147483648` 哨兵 | 未展开时为**相对坐标**（易与屏幕坐标混淆）|
| 按钮 / 复选框 | 有名字的可 `click`；图标按钮 name 全空 | `Press` / `Toggle` 有效，端到端改到 vlcrc；图标按钮 name 全空 |
| 树/列表**接口** | `Accessible, Collection, Component, Selection, Table` | **只有 `Accessible, Component, Table`——无 Selection** |
| 树/列表**选择是否生效** | `Table.add_row_selection(row)` **生效**；cell 的 `activate` action **不生效** | `Table.add_row_selection(row)` 返回 `True` 但**不生效** |
| 单选按钮切换视图 | 未实测 | `Toggle` 改了 CHECKED 状态但**视图不切换**（静默失败）|
| 隐藏控件残留 | 大量 `@-2147483648 1x1` 哨兵节点 | 切换面板后旧控件**留在树里、extents 归零**（`@0,0 0x0`）|

### 实测细节：VLC 的两处静默失败

**(1) Simple / All 单选按钮**

```
调用前: radio button "Simple" [... CHECKED]   radio button "All"  [...]
Toggle("All") -> True
调用后: radio button "Simple" [...]           radio button "All"  [... CHECKED]
```

但窗口标题仍是 `Simple Preferences`，节点数 365 不变，截图确认面板内容一模一样。
**状态变了，行为没变。** 同一坐标 `(173, 738)` 用 xdotool 真点，
立刻切到 `Advanced Preferences`（428 节点）。

机制上最可能的解释（**属推断，未验证源码**）：Qt 的 AT-SPI 桥把 Toggle 实现成
`setChecked()`，只发 `toggled()` 信号；而 VLC 的面板切换处理函数接的是 `clicked()`，
`setChecked()` 不发 `clicked()`。这类"a11y 只改状态、不走点击处理链"的模式
在 Qt 应用里应该是通病而不是 VLC 特例。

**(2) Advanced Preferences 左侧分类树**

```
tree "" @88,136 311x553   interfaces: ['Accessible', 'Component', 'Table']   rows=35 cols=1
add_row_selection(18)  # 第 18 行是 table cell "Interface"
-> True
```

右侧面板仍停在默认的 "Advanced settings"，节点数 428 不变。
坐标点击 `(200, 609)` 则正常展开该分类、右侧切到 "Main interfaces settings"（441 节点）。

**对比 GIMP 的同类操作**：Layers dock 的 `tree table` 有 `Selection` 接口，
`add_row_selection(1)` 调用后活动图层真的切到了 Background（截图验证）。
所以"树选择在 a11y 下不可靠"**不是跨工具包的普遍规律**——
GTK 走对接口就行，Qt 是接口都没暴露、暴露的那个还骗人。

### 一个必须区分的概念：坐标依赖 ≠ 视觉依赖

VLC 上述两处虽然必须"点坐标"，但**坐标是 a11y 自己给的**（`radio button "All"` 的 extents
就是 `@154,729 38x19`）。也就是说这条路是「**a11y 定位 + 合成点击**」，
**不需要截图、不需要 VLM**。真正需要视觉的只有"无名节点的语义识别"：

| 场景 | 需要坐标 | 需要视觉 |
|---|---|---|
| VLC Simple/All 单选、分类树选择 | 是 | **否**（有名字，extents 直接可用）|
| VLC 底部控制条 9 个无名按钮 | 是 | 否（`Playback` 菜单有等价命名项）|
| GIMP 工具箱 26 个无名按钮 | 是 | 否（`Tools` 菜单有等价命名项）|
| GIMP 滤镜对话框匿名 spin button | 否（可 set value）| **是**（或用顺序先验代替）|
| GIMP 画布像素内容 | 是 | **是，且无替代** |

按这个口径，**VLC 15 个可行任务里需要"看画面"的接近 0 个**，需要"a11y 定位 + 坐标点击"的是 3 个
（`215dfd39`、`9195653c`、`d06f0d4d`，每个至少 2 次）；
GIMP 17 个里必须看画布的是 3 个，另有 3 个需要一次性的布局确认。

---

## 五、哪些任务本质上必须走 GUI

按判分口径分三类。注意这里说的是"**checker 认不认**"，不是"OSWorld 规则允不允许用终端"。

### A. 判分只读落盘配置——存在非 GUI 捷径（12 个）

- **VLC 8 个**：`215dfd39`、`386dbd0e`、`5ac2891a`、`8ba5ae7a`、`9195653c`、`a5bbbcd5`、
  `d06f0d4d`、`f3977615`。全部 `result.type == "vlc_config"`，只 grep `~/.config/vlc/vlcrc` 的某一行。
  **OSWorld 自己就是这么改的**——`5ac2891a` 的 `config` 直接用 python 正则往 vlcrc 里写
  `play-and-exit=1` 来构造初始状态。写文件 + 让 postconfig 的 `pkill vlc` 重启即可。
- **GIMP 4 个**：`7767eef2`（gimprc `theme "Light"`）、`7b7617bd`（gimprc `undo-levels 100`）、
  `b148e375`（gimprc `layer-new-name "Square"`）、`d52d6308`（sessionrc `hide-docks yes`）。
  其中 `b148e375` 尤其"名不副实"：指令是"新建一个叫 Square 的图层"，
  但 `layer-new-name` 只记录**上次新建图层用过的名字**，改 gimprc 就能过，图层建没建不影响判分。

### B. 判分读产出文件——理论上可用 CLI，但被 harness 卡住（14 个）

- **GIMP 12 个**（10 个 harness 代导出 + `77b8ab4d` + `dbbf4b99`）：
  前 10 个**实际上必须走 GUI**——harness 的 `Shift+Ctrl+E` 导出的是 GIMP 里当前活动图像，
  用进程外脚本改文件不会被导出。可行的"半 GUI"旁路是 GIMP 内置的
  `Filters > Script-Fu > Console`（作用于打开的图像）。
- **VLC 4 个**：`8f080098`（mp3 与参考音频做 MFCC+DTW 比对）、`aa4b5023`（逐帧感知哈希比对）、
  `fba2c100`（截图与参考图 SSIM）、`efcf0d81`（壁纸与参考图 SSIM，且设了
  `reference_base_result: 0.11` 做归一化）。这些用 ffmpeg / gsettings 也能造出来。

### C. 判分读运行时状态——必须真的在 GUI 里发生（4 个）

- `59f21cfb`：`status.xml` 里 `state == playing` 且文件名匹配 → VLC 进程必须真的在播那个文件。
- `bba3381f`：同上，匹配 HLS URL。
- `8d9fd4e2`：`vm_window_size(app_class_name="vlc") == vm_screen_size` → VLC 窗口必须真的全屏。
- `a746add2`：`~/.config/GIMP/2.10/action-history` 里必须出现 `filters-vignette` →
  必须真的触发过那个 GtkAction。**好消息：实测纯 a11y 的 `click` 就能触发**
  （直接点 `menu item "Vignette..."` 打开了对话框）。

---

## 六、给实现的三条具体建议

1. **`click` 不能只映射到 Action 接口。** 对 `table cell` / `list item` 这类节点，
   必须优先尝试父容器的 `Selection.select_child` 或 `Table.add_row_selection`；
   两者都不生效时**必须降级到合成点击**，而不是因为 `do_action` 返回 `True` 就报成功。
   GIMP 的 5 个图层任务和 VLC 的 3 个 Advanced 首选项任务卡的都是这一点。

2. **动作后必须回读判据，且判据不能只看被操作节点自己的状态。**
   VLC 的 Simple/All 单选是最好的反例：节点自己的 `CHECKED` 变了、`do_action` 返回 `True`，
   但应用行为没变。可用的判据是"树的规模/结构是否变化"
   （365 → 428 节点、窗口标题 Simple → Advanced），这比读单个节点状态可靠得多。

3. **无名图标按钮需要一层"命名旁路"知识，而不是每次都掏 VLM。**
   GIMP 工具箱 26 个、VLC 控制条 9 个按钮全部无名，但两者都有**语义等价的菜单项**。
   与其让 agent 每次截图去认图标，不如在工具层把"选模糊选择工具"直接实现成
   `Tools > Selection Tools > Fuzzy Select` 的菜单调用。

---

## 七、未实测、建议补的项

- VLC 的 `Media > Convert/Save` 对话框链（影响 `8f080098`、`aa4b5023`）。
- VLC / GIMP 的文件选择器（影响 6 + 2 个任务）。
- GIMP `Edit > Preferences` 左侧分类树的选择是否踩 G3 同一个坑。
- GIMP `Filters > Script-Fu > Console` 输入框是否 a11y 可写——若可写，
  这是 GIMP domain 上性价比最高的一条路。
- VLC 的 Hotkeys 页（`386dbd0e`）：热键表格是 QTreeWidget，
  很可能同时踩"无 Selection 接口"和"需要双击进入编辑态"两个坑。
