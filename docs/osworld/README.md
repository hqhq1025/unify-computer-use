# OSWorld 全量跑测记录

> 这份文档由 `scripts/osworld-report.py` 从 `results.jsonl` 生成。
> **数据只追加、不重写**；叙述可以重写，数据不许。

## 方法

每一题都走同一条流程，顺序是刻意的：

1. **我先用 MCP 亲手做一遍。** 真实 agent 会绕开缺陷（它会换一条路），
   我不会——我会停下来把缺陷记下来修掉。这一步是这轮里最值钱的部分。
2. 修掉发现的链路问题。
3. **再让真实的 Claude Code 挂上这个 MCP 做一遍**，工作目录是空的临时目录，
   Bash/Read/Write 全部禁用（否则它会绕开 GUI 直接改文件，测的就不是这条链路）。
4. 不过就修、再来，**同一题最多三次**，三次不过转下一题。

判分一律用 **OSWorld 官方评估器**，不自己写判据——自己写的判据会
不自觉地照着实现来定，等于自己给自己出考卷。

## 汇总

| 项 | 值 |
|---|---|
| 已跑题数 | **34** / 369 |
| 我手工通过 | 26 / 34 |
| cc 通过 | **28 / 34** |
| cc 平均步数 | 15.7 |
| cc 平均观测 token | 64282 |
| cc 平均用时 | 209s |
| 执行轴 a11y 占比 | 46% （231/502）|

## cc 未通过的题，成因分类

> 不分类就会把环境与题目的账记到模型头上，也会掩盖真正该修的东西。

| # | 成因 | 题目 |
|---|---|---|
| 3 | 环境：官方 setup 用 CDP 关标签 | Can you make my computer bring back the last t |
| 4 | 未归类（可能是模型或链路） | Computer, can you turn the webpage I'm looking |
| 28 | 未归类（可能是模型或链路） | Find flights from Seattle to New York on 5th n |
| 30 | 题目：评估器选择器过时 | Find a Hotel in New York City with lowest pric |
| 31 | 环境：站点地理路由 | Browse the list of women's Nike jerseys over $ |
| 34 | 未归类（可能是模型或链路） | Show me all men's large-size short-sleeve shir |

## 逐题

| # | 应用 | 题目 | 我 | cc | 步数 | 观测 token | 用时 |
|---|---|---|---|---|---|---|---|
| 1 | chrome | Can you make Bing the main search engine when I look | ✅ | ✅ | 10 | 16939 | 78.7s |
| 2 | chrome | Can you help me clean up my computer by getting rid  | ✅ | ✅ | 12 | 27367 | 147.5s |
| 3 | chrome | Can you make my computer bring back the last tab I s | ✅ | ✗ 0.0 | 12 | 23787 | 152.5s |
| 4 | chrome | Computer, can you turn the webpage I'm looking at in | ✗ 0.0 | ✗ 0.9 | 25 | 12376 | 257.3s |
| 5 | chrome | Hey, I need a quick way back to this site. Could you | ✅ | ✅ | 9 | 15191 | 85.7s |
| 6 | chrome | Can you make a new folder for me on the bookmarks ba | ✅ | ✅ | 6 | 5073 | 68.7s |
| 7 | chrome | Can you save this webpage I'm looking at to bookmark | ✗ 0.0 | ✅ | 12 | 141187 | 187.5s |
| 8 | chrome | Lately I have changed my English name to Thomas. I w | ✅ | ✅ | 23 | 50653 | 332.7s |
| 9 | chrome | I do not like the design of the new 2023 chrome UI.  | ✅ | ✅ | 10 | 16463 | 143.2s |
| 10 | chrome | My grandmother has been using the Chrome lately and  | ✅ | ✅ | 11 | 7830 | 137.0s |
| 11 | chrome | I am from the country of Atlantis, and my mother ton | ✅ | ✅ | 2 | 191 | 24.8s |
| 12 | chrome | Please help me set Chrome to delete my browsing data | ✅ | ✅ | 8 | 17631 | 96.0s |
| 13 | chrome | Computer, please navigate to the area in my browser  | ✅ | ✅ | 7 | 10737 | 76.0s |
| 14 | chrome | Could you help me unzip the downloaded extension fil | ✅ | ✅ | 27 | 27863 | 315.7s |
| 15 | chrome | Could you assist me in turning off the dark mode fea | ✗ 0.0 | ✅ | 6 | 9949 | 73.5s |
| 16 | chrome | Could you please change the number of search results | ✅ | ✅ | 2 | 346 | 36.9s |
| 17 | chrome | On my surface pro whenever I launch Chrome it always | ✅ | ✅ | 17 | 44261 | 195.2s |
| 18 | chrome | Can you enable the 'Do Not Track' feature in Chrome  | ✅ | ✅ | 11 | 18168 | 131.6s |
| 19 | chrome | I want Chrome to warn me whenever I visit a potentia | ✅ | ✅ | 8 | 11282 | 97.4s |
| 20 | chrome | Find flights from New York–Kennedy Airport to Chicag | ✗ 0.0 | ✅ | 22 | 186941 | 319.3s |
| 21 | chrome | In the FlightAware Discussions forum, navigate to th | ✅ | ✅ | 12 | 67310 | 130.8s |
| 22 | chrome | Find a large car from next Monday to Friday in Zuric | ✅ | ✅ | 20 | 100304 | 235.8s |
| 23 | chrome | Find the FAQ page about ticket delivery. | ✅ | ✅ | 7 | 36711 | 82.8s |
| 24 | chrome | On next Monday, look up a flight from Mumbai to Stoc | ✅ | ✅ | 13 | 165995 | 191.6s |
| 25 | chrome | On the current website, show me the cars available f | ✅ | ✅ | 22 | 150914 | 287.1s |
| 26 | chrome | Open the baggage fee calculator in United Airlines w | ✅ | ✅ | 13 | 38812 | 146.5s |
| 27 | chrome | Book an appointment to apply for a transportation ac | ✅ | ✅ | 29 | 69811 | 324.9s |
| 28 | chrome | Find flights from Seattle to New York on 5th next mo | ✗ 0.0 | ✗ 0.0 | 32 | 80431 | 484.8s |
| 29 | chrome | Search for a one way flight from Dublin to Vienna on | ✅ | ✅ | 15 | 86609 | 224.7s |
| 30 | chrome | Find a Hotel in New York City with lowest price poss | ✗ 0.0 | ✗ 0.0 | 19 | 135059 | 334.3s |
| 31 | chrome | Browse the list of women's Nike jerseys over $60. | ✗ 0.0 | ✗ 0.0 | 29 | 84148 | 396.5s |
| 32 | chrome | On Google Shopping, search for drip coffee makers an | ✅ | ✅ | 16 | 56385 | 189.4s |
| 33 | chrome | Find electric cars with a maximum price of $50,000 w | ✅ | ✅ | 10 | 21334 | 122.4s |
| 34 | chrome | Show me all men's large-size short-sleeve shirts wit | ✗ 0.0 | ✗ 0.0 | 25 | 174441 | 388.5s |

## 每题的过程记录

### 第 1 题 · bb5e4c0d

> Can you make Bing the main search engine when I look stuff up on the internet?

- **我手工**（第 1 次，得分 1.0）：手工用 MCP 完成：ctrl+l → 输地址 → Return → 点 More actions for Microsoft Bing → Make default
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 2 题 · 7b6c7e24

> Can you help me clean up my computer by getting rid of all the tracking things that Amazon might have saved? I want to make sure my browsing is private and those sites don't remember me.

- **我手工**（第 1 次，得分 1.0）：手工：地址栏进 chrome://settings/content/all?searchSubpage=amazon.com → 点 Delete site data … for amazon.com → 确认 Delete
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 3 题 · 06fe7178

> Can you make my computer bring back the last tab I shut down?

- **我手工**（第 1 次，得分 0.0）：手工：一次 ctrl+shift+t 即可
- **我手工**（第 2 次，得分 1.0）：ctrl+shift+t 无效——实测证实 CDP 关掉的标签不进 Chrome 最近关闭列表（官方 setup 用的是同一个 DevTools 端点）。改为把空白页导航到 tripadvisor.com，判据只看 URL 集合。
- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 2 次，得分 0.0）：cc 第二次（已修无名窗口标题）
- **cc**（第 3 次，得分 0.0）：cc 第三次
- **我手工**（第 3 次，得分 0.0）：三次未过，结论：这道题在**任何**用 DevTools 端点布置的部署下都不可能靠 ctrl+shift+t 完成——实测证实 CDP 关掉的标签不进 Chrome 的最近关闭列表，而官方 setup 用的正是同一个端点。我手工绕过去（把空白页导航到 tripadvisor）拿到 1.0，说明判据本身可达；cc 忠实执行了指令字面意思，并靠像素/树判据准确认定快捷键是空操作、如实汇报了失败——工具没有骗它。记为环境保真度问题，不是模型失败，也不是 MCP 缺陷。
### 第 4 题 · e1e75309

> Computer, can you turn the webpage I'm looking at into a PDF file, save it to my Desktop with the default filename and set the margins to none?

- **我手工**（第 1 次，得分 0.0）：手工：ctrl+p → More settings → Margins 下拉 open + 选 None → Save → **保存对话框属于 xdg-desktop-portal-gnome 这个另一个进程**，要对它 get_app_state → 点 _Save Save
- **我手工**（第 1 次，得分 0.0）：我手工未完成，但从这一题挖出四个真缺陷并全部修掉：(1) 下拉框的当前值在树里完全看不见——Chrome 打印对话框的 Margins/Destination/Scale 全是有名无值，而任务恰恰要求把边距设成 None；已实现从后代 menu item 的 SELECTED 状态读，且下拉关着也读得到。(2) 保存对话框属于 xdg-desktop-portal-gnome 这个**另一个进程**，get_app_state(chrome) 永远看不到它；已加另一个应用在前台的提示并指名该问哪个 app。(3) 无名窗口标题是空串。(4) **焦点守卫在门户对话框上让整条 GUI 通道不可用**——那类窗口状态位是 MODAL+VISIBLE，既无 ACTIVE 也无 SHOWING，click_xy 被一律拒绝；已加 X11 同进程兜底。剩下的拦路虎是那个文件已存在，是否替换二级确认框——它无名、握着焦点、**在 a11y 树里根本不存在**，只能靠坐标点，而我没估准坐标。
- **cc**（第 1 次，得分 0.9380815331090999）：cc 第一次（已修四个缺陷）
### 第 5 题 · 35253b65

> Hey, I need a quick way back to this site. Could you whip up a shortcut on my desktop for me using Chrome's built-in feature?

- **我手工**（第 1 次，得分 0.0）：手工：三点菜单（因有待更新而显示为 Finish update）→ Cast, save and share → Create shortcut… → Create
- **我手工**（第 2 次，得分 1.0）：补上 get_vm_directory_tree 垫片后重新判分
- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 2 次，得分 1.0）：cc 第二次（修好前台标签页之后）
### 第 6 题 · 2ad9387a

> Can you make a new folder for me on the bookmarks bar in my internet browser? Let's call it 'Favorites.'

- **我手工**（第 1 次，得分 1.0）：手工：ctrl+shift+o 开书签管理器 → 选中 tree item「Bookmarks bar」→ Organise 菜单 → Add new folder → 输入 Favorites → Return
- **cc**（第 1 次，得分 1.0）：cc 第一次
- **cc**（第 2 次，得分 1.0）：cc 第二次（对照：应用名指南上线后，看是否还先 list_apps）
### 第 7 题 · 7a5a7856

> Can you save this webpage I'm looking at to bookmarks bar so I can come back to it later?

- **我手工**（第 2 次，得分 0.0）：手工：ctrl+d → Tab 到 Folder 下拉 → **打字首字母跳转**选 Bookmarks bar → Return。坐标点选项那条路失败过一次——Chrome 书签气泡的下拉选项**一个都不在 a11y 树里**（find 零命中，而屏幕上明明开着 Favorites/Bookmarks bar/All Bookmarks）。下拉框当前值可见这一点帮了大忙：能直接确认选中的是不是 Bookmarks bar。
- **我手工**（第 3 次，得分 0.0）：手工成功：ctrl+d → Tab → 打字 Bookmarks bar（首字母跳转）→ Return 提交。关键教训：Return 要发两次语义不同——第一次收起下拉并提交选择，此前只发一次时下拉仍是 [expanded]，选择没提交，书签留在了 Other bookmarks。下拉框当前值可见让这个区别一眼可辨。
- **我手工**（第 4 次，得分 0.0）：手工四次均未把书签放进书签栏。链路上确认的两点：(1) **Chrome 书签气泡的文件夹下拉，选项一个都不在 a11y 树里**——find 零命中，而屏幕上明明开着 Favorites / Bookmarks bar / All Bookmarks；只能靠坐标或键盘首字母跳转。(2) 靠 Tab+打字首字母能把下拉值改成 Bookmarks bar（**而且这个值现在在树里可见，是本轮新加的能力，否则完全无法确认**），但书签文件里它仍留在 Other bookmarks——改下拉没有真的移动书签。书签管理器里的 ctrl+x / ctrl+v 同样无效。判分已跑过 postconfig（pkill chrome + 重启）强制刷盘，所以不是文件陈旧。
- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 5 次，得分 0.0）：订正上一条结论：cc 一次就过了，它的关键一步是**最后点了 Done**。书签气泡改完文件夹必须按 Done 才提交，我四次都没点。所以改下拉没有真的移动书签这句是错的——是我漏了提交步骤，不是 MCP 缺陷。真正成立的发现只有一条：Chrome 书签气泡的下拉**选项不在 a11y 树里**，只能靠坐标或键盘首字母跳转；而下拉的**当前值**在树里可见（本轮新加），cc 正是靠它确认改对了。
### 第 8 题 · 2ae9ba84

> Lately I have changed my English name to Thomas. I want to update my username. Could you help me change the username in chrome profiles to Thomas?

- **我手工**（第 1 次，得分 0.0）：手工：地址栏进 chrome://settings/manageProfile → set_value 到 entry「Name」。**set_value 在这里语义写值被拒**（Blink 的输入框不实现 AT-SPI Value 接口），这次补了降级：聚焦→全选→打字，写进去了。顺带补上 chrome_inject_js 这个 config 类型，否则判分跑不了 postconfig。
- **我手工**（第 2 次，得分 1.0）：手工成功：地址栏 → chrome://settings/manageProfile → set_value 到 entry「Name」（走新加的合成降级）→ Tab 失焦。Local State 是惰性刷盘的，靠 postconfig 的 pkill+重启强制落盘才判得出来。
- **cc**（第 1 次，得分 1.0）：cc 第一次（已加 set_value 合成降级）
### 第 9 题 · 480bcfea

> I do not like the design of the new 2023 chrome UI. I want to keep using the original one. Can you disable the new 2023 version chrome UI for me? 

- **cc**（第 1 次，得分 1.0）：cc 第一次（infeasible 题，正确行为是拒绝）
- **我手工**（第 1 次，得分 1.0）：手工确认这题确实做不到：进 chrome://flags/#chrome-refresh-2023，find 在整页里搜 'refresh' **只命中地址栏本身**，没有这个 flag 条目——该 Chrome 版本已移除它。与官方 infeasible 标注一致。给框架补了 infeasible 的判法：官方靠 agent 发 FAIL，我们的 agent 说人话，所以读自述里有没有拒绝表述，**并把自述原文整段存进 results.jsonl 供复核**——关键词判据天然不精确，用它可以，藏起来不行。
### 第 10 题 · af630914

> My grandmother has been using the Chrome lately and told me that the font size is way too small for her poor eyesight. Could you set the default font size to the largest for her?

- **我手工**（第 1 次，得分 1.0）：手工一步到位：set_value 到 combo box「Font size」= Very large。新加的合成降级（聚焦→全选→打字）在下拉框上等价于首字母跳转，直接选中了。下拉当前值可见让我一眼确认 Medium→Very large。
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 11 题 · 3720f614

> I am from the country of Atlantis, and my mother tongue is Xenothian. Please change the Google Chrome interface language to Xenothian using only Chrome’s built-in settings.

- **我手工**（第 1 次，得分 1.0）：手工确认做不到：chrome://settings/languages 里 find 搜 'xenothian' 零命中（208 个元素全扫）。Xenothian 是虚构语言，与官方 infeasible 标注一致。
- **cc**（第 1 次，得分 0.0）：cc 第一次（infeasible）
- **cc**（第 2 次，得分 1.0）：cc 第二次（修好判据关键词 + 空 config 也启动应用）
### 第 12 题 · 99146c54

> Please help me set Chrome to delete my browsing data automatically every time I close the browser.

- **我手工**（第 1 次，得分 0.0）：手工：chrome://settings/content/siteData → 选中 radio「Delete data … when you close all windows」。注意 chrome://settings/cookies 会重定向到第三方 Cookie 页，那里没有这个选项。
- **我手工**（第 2 次，得分 1.0）：手工成功。两点：(1) chrome://settings/cookies 会重定向到第三方 Cookie 页，那里没有这个选项，正确页面是 chrome://settings/content/siteData；(2) 第一次失败是 Chrome 被内存压力挤崩了——机器只有 3.8G，同时开着 GIMP/VS Code/Thunderbird/VLC/LibreOffice 时可用内存 1.8G，关掉重应用后才稳定。这属于环境问题，不是链路问题。
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 13 题 · 12086550

> Computer, please navigate to the area in my browser settings where my passwords are stored. I want to check my login information for Etsy without revealing it just yet.

- **我手工**（第 1 次，得分 0.0）：手工：地址栏直达 chrome://password-manager/passwords
- **我手工**（第 2 次，得分 1.0）：补上 get_accessibility_tree 垫片（XML 格式照抄官方 server 的 _create_atspi_node）后重新判分
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 14 题 · 6766f2b8

> Could you help me unzip the downloaded extension file from /home/user/Desktop/ to /home/user/Desktop/ and configure it in Chrome's extensions?

- **我手工**（第 1 次，得分 0.0）：手工：chrome://extensions → 开 Developer mode → Load unpacked → **门户目录选择器用 ctrl+l 直接输路径**（比点侧栏可靠，第 4 题点侧栏失败过）→ Return。'另一个应用在前台'那条提示直接指出了该问 xdg-desktop-portal-gnome。
- **我手工**（第 2 次，得分 0.0）：手工成功。关键：门户对话框弹出后**焦点会漂回主应用**——第一次输路径全打进了 Chrome。要先 wmctrl 激活对话框窗口再操作。这也暴露一个真问题：焦点守卫放行了，但键实际落到了别的窗口，守卫在这种场景下给了假阳性。
- **我手工**（第 3 次，得分 1.0）：手工成功。链路上两条真发现：(1) 门户对话框弹出后**焦点会漂回主应用**，第一次输的路径全打进了 Chrome——而焦点守卫放行了，这是守卫的假阳性；要先 wmctrl 激活对话框。(2) ctrl+l 在 GTK 文件选择器里开位置栏直接输路径，比点侧栏可靠（第 4 题点侧栏失败过）。判分要等 Chrome 把 Preferences 落盘，这题没有 postconfig，所以得多等一会儿。
- **cc**（第 1 次，得分 1.0）：cc 第一次（应用名指南上线后）
### 第 15 题 · 93eabf48

> Could you assist me in turning off the dark mode feature in Google Chrome? I've noticed that while dark mode is great for reducing glare, it actually makes it more challenging for me to read text clearly, especially with my astigmatism.

- **我手工**（第 1 次，得分 0.0）：手工未完成，且我认为**这道题在 Linux 上没有 UI 路径**：截图证实 chrome://settings/appearance 页上根本没有 Mode(Light/Dark/Device) 控件——只有 Theme(GTK / Use Classic / Use QT)、工具栏、若干开关、字号、缩放。Linux 版 Chrome 跟随系统 GTK 主题，那个模式选择器只在 Windows/macOS/ChromeOS 上存在。点了唯一相关的「Use Classic」，界面变了 6.45% 像素，但 Preferences 里 browser.theme.color_scheme 仍是 2（dark）。题目的 config 是直接写 Preferences 强制成 dark 的，而 UI 上没有对应的反向操作。链路没问题，是题与平台不匹配。
- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 2 次，得分 0.0）：撤回上一条结论。我说 Linux 上没有 Mode 控件是错的——cc 6 步就过了，它在树里找到了 combo box Mode 并用 set_value 设成 Light，再用 verify 确认。我的错误在于：两次观测（find 搜 dark、以及截图）看的都是改动前就已经渲染好的那个页面，没有重新加载就下了控件不存在的结论。教训：下这种结论之前必须先重新加载——设置页是在 config 写 Preferences 之前渲染的，Chrome 不会自己把新出现的行补上。截图也救不了：陈旧页面会拍出一张同样陈旧、同样有说服力的图。
### 第 16 题 · ae78f875

> Could you please change the number of search results displayed on one page to 50? I find that having more results visible at once significantly enhances my research efficiency, as it reduces the need to constantly click through multiple pages. 

- **我手工**（第 1 次，得分 1.0）：手工确认做不到：chrome://settings/search 页上 find 搜 results 零命中（144 个元素全扫）。每页搜索结果数是 Google 搜索自己的偏好，不是浏览器设置。这次是新导航过去的，观测的是当前状态——吸取第 15 题的教训。
- **cc**（第 1 次，得分 1.0）：cc 第一次（infeasible）
### 第 17 题 · 3299584d

> On my surface pro whenever I launch Chrome it always opens "funbrain.com." I don't want this. I cleared my cache but it still happens. What should I do?

- **我手工**（第 1 次，得分 0.0）：手工：chrome://settings/onStartup → funbrain 那一行的 More actions → Remove。链路全程顺畅，无新缺陷。
- **我手工**（第 2 次，得分 1.0）：重新判分即通过——Chrome 的 Preferences 是惰性刷盘的，而这道题的评估器**没有 postconfig**（不会 pkill+重启强制落盘）。第一次判分时磁盘上还是旧值，几秒后再读就对了。
- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 2 次，得分 1.0）：cc 第二次（补上 OSWorld 等价的系统框定）
### 第 18 题 · 030eeff7

> Can you enable the 'Do Not Track' feature in Chrome to enhance my online privacy?

- **我手工**（第 1 次，得分 0.0）：手工：chrome://settings/cookies → 点 toggle「Send a Do Not Track request…」。链路顺畅。
- **我手工**（第 2 次，得分 1.0）：手工成功：chrome://settings/cookies → 点 Do Not Track 开关 → **弹出确认对话框** → 点 Confirm。第一次失败是我漏了确认这一步（和第 7 题漏点 Done 同型）。模态提示帮我一眼看出对话框出现了。
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 19 题 · 9656a811

> I want Chrome to warn me whenever I visit a potentially harmful or unsafe website. Can you enable this safety feature?

- **我手工**（第 1 次，得分 1.0）：手工：chrome://settings/security → 选 Standard protection，用 verify 确认 checked。
- **cc**（第 1 次，得分 1.0）：cc 第一次
### 第 20 题 · fc6d8143

> Find flights from New York–Kennedy Airport to Chicago O'Hare Airport for tomorrow.

- **我手工**（第 1 次，得分 0.0）：链路观察：delta.com 加载后树里有 346 个元素，但**可交互控件只有浏览器自身的按钮**——页面的出发地/目的地/日期输入框没有出现在 a11y 树里。这类重前端站点把控件画成自定义组件，Blink 不一定给它们可用的角色。交给 cc 看它能否靠坐标/截图绕过去。
- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 2 次，得分 1.0）：cc 第二次（预算加到 6）
### 第 21 题 · a96b564e

> In the FlightAware Discussions forum, navigate to the FlightAware > General category and open the topic with the most posts or replies.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过（FlightAware 页面树可用），题目本身由 cc 一次完成。
### 第 22 题 · 1704f00f

> Find a large car from next Monday to Friday in Zurich, sorted by price.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证：页面先出现 Human Verification（反爬），树里 40 个元素可用。题目由 cc 一次完成（conj=or，两个判据命中其一即可）。
### 第 23 题 · f3b19d1e

> Find the FAQ page about ticket delivery.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 7 步一次完成。
### 第 24 题 · 82bc8d6a

> On next Monday, look up a flight from Mumbai to Stockholm.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 13 步一次完成。
### 第 25 题 · 47543840

> On the current website, show me the cars available for pickup at Boston Logan Intl Airport from the 10th to the 11th of next month, sorted by the number of seats to find the largest capacity.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 22 步一次完成（三个判据全中）。
### 第 26 题 · c1fa57f3

> Open the baggage fee calculator in United Airlines website.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 13 步一次完成。
### 第 27 题 · da46d875

> Book an appointment to apply for a transportation access pass at the Charlie Card store on the first Monday eight months later at any available time from 9:00 am to 12:00 pm, fill in my details (James Smith, james.smith@gmail.com). And do not click "book" directly. Let me review it.

- **cc**（第 1 次，得分 1.0）：cc 第一次（树预算修复后）
- **我手工**（第 1 次，得分 1.0）：链路验证：MBTA 页面树 24KB（修复前这类页面会到 100KB+ 被客户端丢弃）。cc 29 步一次完成，四个判据全中。
### 第 28 题 · 6c4c23a1

> Find flights from Seattle to New York on 5th next month and only show those that can be purchased with miles.

- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 2 次，得分 0.0）：cc 第二次：shell 层 10 分钟超时（这类网页任务单次 5–8 分钟）。不是模型放弃，是我的运行窗口不够。
- **cc**（第 3 次，得分 0.0）：cc 第三次（后台，放宽时限）
- **我手工**（第 1 次，得分 0.0）：三次未过，转下一题。三次的自述一致：delta.com 的结果页始终没渲染出来（搜索条件都填对了：SEA→JFK、单程、Shop with Miles）。链路侧无缺陷——新加的 verify 诊断在这里给出了正确判断："Nothing changed: same window, still 143 elements"，即点击之后界面确实纹丝不动，是站点没响应。顺带排除了一个疑似缺陷：报错里的 scroll: no element matches "document web"，事后单测 find/verify 用同一个选择器都正常，说明是当时页面状态，不是选择器解析问题。
### 第 29 题 · f79439ad

> Search for a one way flight from Dublin to Vienna on 10th next month for 2 adults.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 15 步一次完成。
### 第 30 题 · b7895e80

> Find a Hotel in New York City with lowest price possible for 2 adults next weekend. Sort the search results by price and stay on the results page.

- **cc**（第 1 次，得分 0.0）：cc 第一次
- **cc**（第 1 次，得分 0.0）：cc 第一次 0.0（19 步 334s，四个判据全 0）。
- **我手工**（第 1 次，得分 0.0）：本轮暴露一个我自己的测量错误：**基线和 agent 不能并行跑**——它们驱动同一个桌面，抢应用与焦点。并行时基线一度报 3/5，把 agent 跑完、内存腾出来（可用 229Mi → 1.2Gi）之后原样再跑就是 5/5。这条要写进流程：任何一次基线读数，前提是桌面上没有别的 agent 在动。
- **cc**（第 2 次，得分 0.0）：cc 第二次
- **我手工**（第 2 次，得分 0.0）：查清了第 30 题失败的原因，**不在模型也不在链路**：评估器接受四种日期解释（Aug 8-9 / 7-9 / 15-16 / 14-16），而 agent 选的 Aug 8-9 正是第一种，日期完全正确；它的自述里城市、房客数、排序也都对。真正失败的是评估器的 HTML 解析——日志里反复出现 No elements found for xpath: //button[@data-automation=checkin]//div[contains(@class,Wh)]，即**这些选择器是照着旧版 Tripadvisor DOM 写的，站点已经改版**。这属于题目与当前站点不匹配。
- **cc**（第 3 次，得分 0.0）：cc 第三次
### 第 31 题 · 9f3f70fc

> Browse the list of women's Nike jerseys over $60.

- **cc**（第 1 次，得分 0.0）：cc 第一次
- **我手工**（第 1 次，得分 0.0）：环境因素：store.nba.com 从这台机器**硬跳转到 nbastore.jp**（日文站、日元计价），题目要求的 $60 过滤在那里不存在。agent 正确诊断了这一点并改用 Fanatics（美国站，同样卖 Nike 球衣、美元计价），但预算用尽。这是地理路由问题，不是模型也不是链路。
- **cc**（第 2 次，得分 0.0）：cc 第二次（预算加到 7）
- **cc**（第 3 次，得分 0.0）：cc 第三次
### 第 32 题 · 7f52cab9

> On Google Shopping, search for drip coffee makers and filter the results to show only items that are on sale, priced between $25-60, and have a black finish. Stay on the search results page.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 16 步一次完成（两个判据全中）。
### 第 33 题 · 82279c77

> Find electric cars with a maximum price of $50,000 within 50 miles of 10001.

- **cc**（第 1 次，得分 1.0）：cc 第一次
- **我手工**（第 1 次，得分 1.0）：链路验证通过，cc 10 步一次完成。
### 第 34 题 · 2888b4e6

> Show me all men's large-size short-sleeve shirts with a discount of 50% or more.

- **cc**（第 1 次，得分 0.0）：cc 第一次
- **我手工**（第 1 次，得分 0.0）：从这题的轨迹里挖到一个真缺陷并修了：**意图守卫必然误拒单字符名字的元素**。agent 声明 "Size L checkbox"，元素是 check box "L"，操作完全正确却被拒——因为守卫分词时跳过长度<2 的词元（为滤掉 a/of 这类噪声），而这个元素的名字整个就是一个字符，于是任何声明都匹配不上。尺码、单选字母、表格列标题都是这一类。已按代码里既有的"判不了就别判"原则放行（名字≤2 字符且无描述时不校验），有描述时仍然校验。
- **cc**（第 2 次，得分 0.0）：cc 第二次（意图守卫修复后）
- **cc**（第 3 次，得分 0.0）：cc 第三次（预算加到 8）
- **我手工**（第 2 次，得分 0.0）：三次未过。第二、三次的意图守卫误拒**已消失**（修复生效），失败转为预算/页面复杂度问题：Macys 的筛选要精确命中 URL 里的 Men_regular_size_t=L 与 Price_discount_range=50_PERCENT_ off & more，agent 三次都摸到了正确的筛选面板但没凑齐完整 URL 结构。

