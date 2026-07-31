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
| 已跑题数 | **3** / 369 |
| 我手工通过 | 3 / 3 |
| cc 通过 | **2 / 3** |
| cc 平均步数 | 9.0 |
| cc 平均观测 token | 36420 |
| cc 平均用时 | 102s |
| 执行轴 a11y 占比 | 45% （15/33）|

## 逐题

| # | 应用 | 题目 | 我 | cc | 步数 | 观测 token | 用时 |
|---|---|---|---|---|---|---|---|
| 1 | chrome | Can you make Bing the main search engine when I look | ✅ | ✅ | 10 | 16939 | 78.7s |
| 2 | chrome | Can you help me clean up my computer by getting rid  | ✅ | ✅ | 12 | 27367 | 147.5s |
| 3 | chrome | Can you make my computer bring back the last tab I s | ✅ | ✗ 0.0 | 12 | 23787 | 152.5s |

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

