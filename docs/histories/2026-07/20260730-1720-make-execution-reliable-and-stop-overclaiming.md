## [2026-07-30 17:20] | Task: 让执行变得可靠——工具不许替 agent 打包票，静默无操作时自动接管

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04 + X11 GNOME 会话 + at-spi2-core 2.44 + GIMP 2.10.30 + LibreOffice 7.3 + Nautilus 42.6）`

### 📥 User Query
> 我们现在要做的就是让执行变得可靠
>
> 至少这个mcp/tool不能骗agents 应该反应真实状态

### 🛠 Changes Overview
**Scope:** `apps/OpenComputerUseLinux/main.go`（动作后校验与重试、指针闸门、
serverInstructions）、`runtime.py`（对外话术、应用解析重试）、两侧测试。

**Key Actions:**
- **语义调用不再报成成功**：新增 `UNVERIFIED_SEMANTIC`，所有 `do_action` 类
  Note 都附上"工具包接受了调用 ≠ 动作生效"。
- **`[clickable]` 改名 `[has-click-action]`**：它保证的是"有一个动作"，不是"点得动"。
- **`auto` 在静默无操作时自动改走坐标合成**：`actionResult` 里用外部信号判定，
  无变化则对同一元素重发坐标点击。
- **指针闸门按"是否锚定元素"分流**：带 `element_index` 的 `global` 放行。
- **`resolve_app` 加重试**：最多 3 次、间隔 0.3s。

### 🧠 Design Intent (Why)

#### 贯穿今天所有实测的一条线：a11y 对「定位」可靠，对「执行」不可靠

证据来自四个互不相干的来源，且是**独立收敛**的：

| 来源 | 现象 |
|---|---|
| Nautilus（本人实测） | 文件图标的 `menu` 动作返回 True，菜单一个不弹；未选中 / `grab_focus` 后 / `Selection.select_child` 后三种前置全试过 |
| GIMP（调研 agent 实测，本轮独立复现） | 图层 cell 的 `activate` 返回 True，活动图层不变 |
| VLC（调研 agent 实测） | 单选按钮 `Toggle` 后 `CHECKED` **真的翻转了**，面板却不切换 |
| OSWorld 官方代码 | `show-thunderbird-attachments.py` 用选择器定位到按钮后，仍读 bbox 算中心点做 `pyautogui.click` |

VLC 那条给出了判据设计的硬约束：**动作是否生效，不能读被操作节点自身的状态**，
状态会跟着变、行为没有。

#### 一、工具不许替 agent 打包票

原来的 Note 是 `"Invoked the element's AT-SPI accessibility action."` 且
`isError=false`。实际只知道 `do_action` 返回了 True——而上表说明这不等于生效。
把它当成功上报，等于向 agent 谎报事实：它会据此推进下一步，而真实界面停在原地。

三处话术改成只说已知为真的事。**其中一处是我自己本轮早些时候写进去的**：
`serverInstructions` 里 "tells you in advance whether an element-targeted click
**will work**"——同一个谎。`[clickable]` 这个标记名同理，读起来像"点这里就行"。

**没有一律加免责声明**：`type_text` / `set_value` 那两条是真的回读校验过的，
且早已带着"控件变了 ≠ 应用采纳了"的限制说明，原样保留。
诚实不等于处处示弱，该确定的地方就说确定。

#### 二、agent 撞到撒谎动作时，原先一条路都没有

```
accessibility        -> do_action 返回 True，报成功
auto                 -> 只在 do_action 返回 False 时回落，这里不回落
app_post / sky_click -> Linux 不支持
global               -> 被 OPEN_COMPUTER_USE_ALLOW_GLOBAL_POINTER_FALLBACKS 拦下
```

而那道闸门本身是**不对称**的：`auto` 的回落分支合成的是同样的坐标点击，却不受
该开关约束。所以它挡不住指针移动，只挡住了 agent **主动选择**合成的能力。
判据改成按"是否锚定到元素"分流：落点由无障碍树给出的与 `auto` 回落完全等价，
裸坐标才是它真正要拦的。

#### 三、自动接管的判据与它的边界

`actionResult` 在窗口标题、整棵树、焦点、选中**全都逐行不变**时，对同一元素
重发一次坐标点击。退出机制不新增参数——`accessibility` 就是"只走语义"，
`global` 就是"只合成"，两个显式选项本来就在 schema 里（仓库有工具面对齐约束，
能不动协议面就不动）。

**重复执行的风险是可控的**：只有应用状态与动作前完全一致时才重试，此时再点一次
等价于从同一状态点第一次。真正会漏判的是"生效了但界面毫无痕迹"，
而当前的失败模式——静默无操作——实测在三个应用上都会发生。

**边界已写进代码，不含糊**：这个机制只接住"什么都没变"，**接不住 VLC 那种
"状态变了、行为没变"**——`CHECKED` 翻转会带动树变化，判据就认为发生了变化。
要接住那一类，需要知道这次动作**本该**造成什么后果，那是任务级语义，
通用判据给不出来。

### ✅ Verification

**端到端真机验证（GIMP 图层面板）**，判据用**独立于被操作节点**的状态栏：

```
动作前  status bar: 'TopLayer (1.2 MB)'

click(element_index=122 "table cell Background", click_method="auto")
  Note: [semantic] Invoked the element's AT-SPI accessibility action.
        The toolkit accepted the call; that is not evidence the action took effect…
  Note: The semantic action reported success but nothing observably changed,
        so this retried the same element as a coordinate click.
  Note: [synthesis] Synthesized a coordinate click at (1891, 600)…

动作后  status bar: 'Background (1.2 MB)'      ← 活动图层真的切换了
```

同时独立复现了调研 agent 的结论：GIMP 图层 cell 的 `activate` 确实返回 True
却什么都不做。

单测覆盖触发条件的五种情形：auto + 元素 + 语义通道才重试；`accessibility`
不代劳；已走过合成不重试；无元素锚点不凭空合成坐标；只对 `click` 开放。
Go 侧新断言与 `resolve_app` 重试测试均已确认在改动前的代码上失败。
`./scripts/ci.sh` 全绿，94 个 Python 单测通过。

### 📌 Notes

**更正一处我自己夸大的证据。** 先前把 LibreOffice 确认框的 `Yes` 也列为撒谎动作，
本轮复现时**它语义调用就成功了**（恢复对话框正常关闭、Writer 打开了文件）。
那次失败发生在屏幕上堆了 5 个同样弹窗的时候（我反复点击叠出来的），
很可能是 `main_window()` 选中了非顶层的那个。相关注释与断言已全部改回三个
证据确凿的案例。刚做完一轮"工具不许打包票"，自己更不能在证据上打折扣。

**`description` 的价值在 GIMP 上再次得到印证**：工具箱 26 个按钮全是无名 `panel`，
唯一的身份就是描述（`Move Tool` / `Crop Tool` / `Eraser Tool` / `Text Tool`）。
没有本轮的 description 渲染，GIMP 整个工具箱对 agent 就是一堆无法区分的空节点。

**`resolve_app` 的 `appNotFound` 未能钉死根因**：三次失败都发生在会开关对话框的
点击之后，而空名字与桌面条目消失两种猜想都实测证伪了。重试是对症的处置——
"暂时读不到"不等于"不存在"——但根因仍然待查。
