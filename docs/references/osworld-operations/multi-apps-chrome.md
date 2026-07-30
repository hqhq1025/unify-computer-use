# OSWorld `multi_apps` / `chrome` 两个 domain 的 GUI 操作调研

本文回答四个问题：multi_apps 的应用组合分布（A）、跨应用数据交接点（B）、chrome domain 中纯浏览器任务的比例（C）、multi_apps 中完全不涉及浏览器的任务数（D）。

结论服务于本项目的分层架构：浏览器交给独立控制平面（Playwright / browser-use），桌面应用交给 AT-SPI MCP。B 节是两者接缝的实测画像。

## 数据来源与统计口径

### 数据来源

- `/home/user/OSWorld/evaluation_examples/examples/multi_apps/`
- `/home/user/OSWorld/evaluation_examples/examples/chrome/`

每个任务是一个 JSON，本文用到四个字段：

| 字段 | 含义 |
|---|---|
| `instruction` | 自然语言任务描述 |
| `related_apps` | 官方标注的涉及应用 |
| `config` | 初始环境搭建步骤（下载文件、启动应用、开标签页……） |
| `evaluator` | 判分方式（`func` + `result` 取数通道 + `expected`） |

### 任务计数

- **multi_apps = 101**，**chrome = 46**。
- `multi_apps/` 目录下有 102 个文件，但其中 `7f35355e-02a6-45b5-b140-f0be698bcf85_result_gold.txt` 是一个判分辅助文本，不是任务。`ls *.json` 得 101。
- 与官方清单一致：`evaluation_examples/test_all.json` 里 `multi_apps` 101 条、`chrome` 46 条。
- 全文所有百分比分母：multi_apps 用 101，chrome 用 46。

### `related_apps` 归一化规则

原始标注大小写和命名不统一，统计前做如下归一：

| 原始值 | 归一为 |
|---|---|
| `Chrome`、`browser` | `chrome` |
| `OS` | `os` |
| `vscode` | `vs_code` |
| `writer`、`Writer` | `libreoffice_writer` |
| `calc` | `libreoffice_calc` |

保持独立不合并的标签：`terminal`（gnome-terminal 是独立窗口，与"文件管理器/系统设置"性质不同）、`pdf`、`image`（这两个其实是文档类型标记而非应用）、`picard`、`ubuntu_media_player`、`libreoffice`（泛指）。

### 标注口径

A 节的组合表直接来自归一化后的 `related_apps`，可完全机器复现。

B / C / D 节的分类是**逐条阅读 101 + 46 条 `instruction` + `config` + `evaluator` 后的人工标注**，不是关键词自动分类。每条任务的标注结果见文末附录，可逐条追溯。三个口径定义如下：

**浏览器参与度**（用于 B、D）：

| 标签 | 定义 |
|---|---|
| **必需** | 指令或判分明确要求访问网页 / 下载 / 上传 Drive / 检查 tab、书签、扩展 |
| **仅背景** | `config` 启动了 Chrome 或 `related_apps` 标了 chrome，但产出与判分全在桌面侧，浏览器至多用来查资料 |
| **不涉及** | 其余 |

交叉验证：`related_apps` 含 `chrome`/`browser` 的任务共 44 条；本文判为"必需 + 仅背景"的共 45 条。差集只有一条（`3f05f3b9`，`related_apps` 是 `os+picard`，但 `config` 里有 `chrome_open_tabs`），且**没有任何一条 `related_apps` 标了 chrome 却被本文判为"不涉及"**。即本文的判定是官方标注的严格超集，不存在漏判。

**交接方式**（用于 B，一条任务可有多个标签）：

| 代号 | 含义 |
|---|---|
| `FS` | 文件系统路径：应用 A 写文件到磁盘，应用 B 按路径读 |
| `DL` | 浏览器下载 / 另存 / 打印为 PDF → 落到本地文件系统 |
| `UP` | 本地文件 → 上传到网页（Google Drive、在线工具） |
| `W2D` | 网页内容（不产生文件）→ 誊抄 / 粘贴进本地文档或表格 |
| `D2W` | 本地文件内容 → 驱动浏览器（URL 列表、搜索词、待装插件名单、文件选择器路径） |
| `CLIP` | 显式剪贴板（指令明说 copy/paste，或判分目标就是剪贴板内容） |
| `CLI` | 终端命令行驱动 GUI 应用（`soffice --convert-to`、`code .`、`gimp`、`kill`） |
| `MAIL` | 邮件客户端 ↔ 文件系统（附件导出、邮件导出） |
| `SYS` | 系统级设置（壁纸、默认播放器、GNOME 主题） |
| `NONE` | 无真正跨应用数据流（单应用任务，或"在同一台机器上分别做几件不相干的事"） |

**chrome domain 归类**（用于 C）：

| 代号 | 含义 |
|---|---|
| **纯网页** | 只需操作网页 DOM，浏览器控制平面可全权处理 |
| **浏览器自身 UI** | `chrome://settings`、书签管理器、历史、profile、扩展页——概念上仍在浏览器内，但不是普通网页 DOM |
| **需桌面侧** | 必须动到文件系统、原生对话框或桌面快捷方式 |
| **infeasible** | 官方标注为不可完成，`evaluator.func == "infeasible"` |

## A. multi_apps 的应用组合分布

101 个任务归一化后共出现 **46 种不同的应用组合**、**15 个不同应用**。组合极度长尾：出现 ≥2 次的只有 22 种，另有 24 种组合只出现 1 次。

### A.1 单应用出现频次

| 应用 | 出现任务数 | 占 101 |
|---|---|---|
| `os` | 62 | 61% |
| `chrome` | 44 | 44% |
| `libreoffice_calc` | 31 | 31% |
| `libreoffice_writer` | 27 | 27% |
| `thunderbird` | 12 | 12% |
| `vs_code` | 11 | 11% |
| `gimp` | 10 | 10% |
| `libreoffice_impress` | 8 | 8% |
| `terminal` | 7 | 7% |
| `vlc` | 6 | 6% |
| `pdf` | 5 | 5% |
| `image` | 3 | 3% |
| `picard` | 1 | 1% |
| `ubuntu_media_player` | 1 | 1% |
| `libreoffice` | 1 | 1% |

`os` 出现在 61% 的任务里，但它多数时候不是"一个应用"，而是"文件管理器 / 文件系统 / 系统设置"这个隐含底座——这正好呼应 B 节的结论。

### A.2 完整组合频次表（全部 46 种）

| 组合 | 次数 | 示例任务 id |
|---|---|---|
| chrome + os | 14 | `0e5303d4-8820-42f6-b18d-daf7e633de21`, `26660ad1-6ebb-4f59-8cba-a8432dfe8d38` |
| chrome + libreoffice_calc | 7 | `3e3fc409-bff3-4905-bf16-c968eee3f807`, `4e9f0faf-2ecc-4ae8-a804-28c9a75d1ddc` |
| chrome + thunderbird | 6 | `46407397-a7d5-4c6b-92c6-dbe038b1457b`, `58565672-7bfe-48ab-b828-db349231de6b` |
| libreoffice_calc + os | 5 | `2373b66a-092d-44cb-bfd7-82e86e7a3b4d`, `3a93cae4-ad3e-403e-8c12-65303b271818` |
| chrome + libreoffice_calc + os | 4 | `0c825995-5b70-4526-b663-113f4c999dd2`, `6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a` |
| chrome + libreoffice_writer | 4 | `22a4636f-8179-4357-8e87-d1743ece1f81`, `236833a3-5704-47fc-888c-4f298f09f799` |
| libreoffice_writer + os | 4 | `02ce9a50-7af2-47ed-8596-af0c230501f8`, `1f18aa87-af6f-41ef-9853-cdb8f32ebdea` |
| chrome + libreoffice_writer + os | 3 | `7ff48d5b-2df2-49da-b500-a5150ffc7f18`, `873cafdd-a581-47f6-8b33-b9696ddb7b05` |
| gimp + os | 3 | `3c8f201a-009d-4bbe-8b65-a6f8b35bb57f`, `91190194-f406-4cd6-b3f9-c43fac942b22` |
| os + vs_code | 3 | `26150609-0da3-4a7d-8868-0faf9c5f01bb`, `9219480b-3aed-47fc-8bac-d2cffc5849f7` |
| chrome + os + vs_code | 2 | `69acbb55-d945-4927-a87b-8480e1a5bb7e`, `e2392362-125e-4f76-a2ee-524b183a3412` |
| gimp + libreoffice_writer + os | 2 | `09a37c51-e625-49f4-a514-20a773797a8a`, `227d2f97-562b-4ccb-ae47-a5ec9e142fbb` |
| gimp + vs_code | 2 | `42f4d1c7-4521-4161-b646-0a8934e36081`, `e8172110-ec08-421b-a6f5-842e6451911f` |
| libreoffice_calc + libreoffice_writer + os | 2 | `00fa164e-2612-4439-992e-157d019a8436`, `81c425f5-78f3-4771-afd6-3d2973825947` |
| libreoffice_calc + os + thunderbird | 2 | `415ef462-bed3-493a-ac36-ca8c6d23bf1b`, `f5c13cdd-205c-4719-a562-348ae5cd1d91` |
| libreoffice_calc + terminal | 2 | `3680a5ee-6870-426a-a997-eba929a0d25c`, `ee9a3c83-f437-4879-8918-be5efbb9fac7` |
| libreoffice_calc + thunderbird | 2 | `c867c42d-a52d-4a24-8ae3-f75d256b5618`, `d9b7c649-c975-4f53-88f5-940b29c47247` |
| libreoffice_impress + libreoffice_writer | 2 | `51f5801c-18b3-4f25-b0c3-02f85507a078`, `eb303e01-261e-4972-8c07-c9b4e7a4922a` |
| libreoffice_impress + vlc | 2 | `47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5`, `778efd0a-153f-4842-9214-f05fc176b877` |
| libreoffice_writer + terminal | 2 | `2b9493d7-49b8-493a-a71b-56cd1f4d6908`, `f7dfbef3-7697-431c-883a-db8583a4e4f9` |
| os + terminal | 2 | `2c9fc0de-3ee7-45e1-a5df-c86206ad78b5`, `716a6079-22da-47f1-ba73-c9d58f986a38` |
| os + thunderbird | 2 | `aceb0368-56b8-4073-b70e-3dc9aee184e0`, `c2751594-0cd5-4088-be1b-b5f2f9ec97c4` |
| (空标注) | 1 | `2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e` |
| chrome + image + os | 1 | `ce2b64a2-ddc1-4f91-8c7d-a88be7121aac` |
| chrome + libreoffice + os | 1 | `e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56` |
| chrome + libreoffice_calc + libreoffice_writer | 1 | `da52d699-e8d2-4dc5-9191-a2199e0b6a9b` |
| chrome + pdf | 1 | `a82b78bb-7fde-4cb3-94a4-035baf10bcf0` |
| gimp + libreoffice_impress + os | 1 | `4c26e3f3-3a14-4d86-b44a-d3cedebbb487` |
| gimp + os + pdf | 1 | `a503b07f-9119-456b-b75d-f5146737d24f` |
| gimp + vlc | 1 | `2fe4b718-3bd7-46ec-bdce-b184f5653624` |
| image + libreoffice_calc + os + pdf | 1 | `8e116af7-7db7-4e35-a68b-b0939c066c78` |
| image + os | 1 | `82e3c869-49f6-4305-a7ce-f3e64a0618e7` |
| libreoffice_calc + libreoffice_impress + libreoffice_writer + os | 1 | `869de13e-bef9-4b91-ba51-f6708c40b096` |
| libreoffice_calc + libreoffice_impress + libreoffice_writer + vlc | 1 | `6d72aad6-187a-4392-a4c4-ed87269c51cf` |
| libreoffice_calc + libreoffice_writer | 1 | `bc2b57f3-686d-4ec9-87ce-edf850b7e442` |
| libreoffice_calc + os + pdf | 1 | `185f29bd-5da0-40a6-b69c-ba7f4e0324ef` |
| libreoffice_calc + vs_code | 1 | `7f35355e-02a6-45b5-b140-f0be698bcf85` |
| libreoffice_impress + libreoffice_writer + os | 1 | `bb83cab4-e5c7-42c7-a67b-e46068032b86` |
| libreoffice_writer | 1 | `5bc63fb9-276a-4439-a7c1-9dc76401737f` |
| libreoffice_writer + os + vs_code | 1 | `20236825-b5df-46e7-89bf-62e1d640a897` |
| libreoffice_writer + vs_code | 1 | `98e8e339-5f91-4ed2-b2b2-12647cb134f4` |
| os + pdf | 1 | `337d318b-aa07-4f4f-b763-89d9a2dd013f` |
| os + picard | 1 | `3f05f3b9-29ba-4b6b-95aa-2204697ffc06` |
| os + ubuntu_media_player + vlc | 1 | `9f3bb592-209d-43bc-bb47-d77d9df56504` |
| os + vlc | 1 | `937087b6-f668-4ba6-9110-60682ee33441` |
| terminal + vs_code | 1 | `510f64c8-9bcc-4be1-8d30-638705850618` |

### A.3 两两共现频次（≥2 次；共 42 对，其中 26 对 ≥2 次）

三应用以上的任务会在这里被拆成多对计数，因此本表的用途是看"哪两个应用最常同时出场"，与 A.2 不冲突。

| 应用对 | 共现次数 | 示例任务 id |
|---|---|---|
| chrome + os | 25 | `0c825995-5b70-4526-b663-113f4c999dd2`, `0e5303d4-8820-42f6-b18d-daf7e633de21` |
| libreoffice_calc + os | 16 | `00fa164e-2612-4439-992e-157d019a8436`, `0c825995-5b70-4526-b663-113f4c999dd2` |
| libreoffice_writer + os | 14 | `00fa164e-2612-4439-992e-157d019a8436`, `02ce9a50-7af2-47ed-8596-af0c230501f8` |
| chrome + libreoffice_calc | 12 | `0c825995-5b70-4526-b663-113f4c999dd2`, `3e3fc409-bff3-4905-bf16-c968eee3f807` |
| chrome + libreoffice_writer | 8 | `22a4636f-8179-4357-8e87-d1743ece1f81`, `236833a3-5704-47fc-888c-4f298f09f799` |
| gimp + os | 7 | `09a37c51-e625-49f4-a514-20a773797a8a`, `227d2f97-562b-4ccb-ae47-a5ec9e142fbb` |
| chrome + thunderbird | 6 | `46407397-a7d5-4c6b-92c6-dbe038b1457b`, `58565672-7bfe-48ab-b828-db349231de6b` |
| libreoffice_calc + libreoffice_writer | 6 | `00fa164e-2612-4439-992e-157d019a8436`, `6d72aad6-187a-4392-a4c4-ed87269c51cf` |
| os + vs_code | 6 | `20236825-b5df-46e7-89bf-62e1d640a897`, `26150609-0da3-4a7d-8868-0faf9c5f01bb` |
| libreoffice_impress + libreoffice_writer | 5 | `51f5801c-18b3-4f25-b0c3-02f85507a078`, `6d72aad6-187a-4392-a4c4-ed87269c51cf` |
| libreoffice_calc + thunderbird | 4 | `415ef462-bed3-493a-ac36-ca8c6d23bf1b`, `c867c42d-a52d-4a24-8ae3-f75d256b5618` |
| os + pdf | 4 | `185f29bd-5da0-40a6-b69c-ba7f4e0324ef`, `337d318b-aa07-4f4f-b763-89d9a2dd013f` |
| os + thunderbird | 4 | `415ef462-bed3-493a-ac36-ca8c6d23bf1b`, `aceb0368-56b8-4073-b70e-3dc9aee184e0` |
| image + os | 3 | `82e3c869-49f6-4305-a7ce-f3e64a0618e7`, `8e116af7-7db7-4e35-a68b-b0939c066c78` |
| libreoffice_impress + os | 3 | `4c26e3f3-3a14-4d86-b44a-d3cedebbb487`, `869de13e-bef9-4b91-ba51-f6708c40b096` |
| libreoffice_impress + vlc | 3 | `47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5`, `6d72aad6-187a-4392-a4c4-ed87269c51cf` |
| chrome + vs_code | 2 | `69acbb55-d945-4927-a87b-8480e1a5bb7e`, `e2392362-125e-4f76-a2ee-524b183a3412` |
| gimp + libreoffice_writer | 2 | `09a37c51-e625-49f4-a514-20a773797a8a`, `227d2f97-562b-4ccb-ae47-a5ec9e142fbb` |
| gimp + vs_code | 2 | `42f4d1c7-4521-4161-b646-0a8934e36081`, `e8172110-ec08-421b-a6f5-842e6451911f` |
| libreoffice_calc + libreoffice_impress | 2 | `6d72aad6-187a-4392-a4c4-ed87269c51cf`, `869de13e-bef9-4b91-ba51-f6708c40b096` |
| libreoffice_calc + pdf | 2 | `185f29bd-5da0-40a6-b69c-ba7f4e0324ef`, `8e116af7-7db7-4e35-a68b-b0939c066c78` |
| libreoffice_calc + terminal | 2 | `3680a5ee-6870-426a-a997-eba929a0d25c`, `ee9a3c83-f437-4879-8918-be5efbb9fac7` |
| libreoffice_writer + terminal | 2 | `2b9493d7-49b8-493a-a71b-56cd1f4d6908`, `f7dfbef3-7697-431c-883a-db8583a4e4f9` |
| libreoffice_writer + vs_code | 2 | `20236825-b5df-46e7-89bf-62e1d640a897`, `98e8e339-5f91-4ed2-b2b2-12647cb134f4` |
| os + terminal | 2 | `2c9fc0de-3ee7-45e1-a5df-c86206ad78b5`, `716a6079-22da-47f1-ba73-c9d58f986a38` |
| os + vlc | 2 | `937087b6-f668-4ba6-9110-60682ee33441`, `9f3bb592-209d-43bc-bb47-d77d9df56504` |

### A.4 每个任务涉及几个应用

| 应用数 | 任务数 |
|---|---|
| 0 | 1 |
| 1 | 1 |
| 2 | 72 |
| 3 | 24 |
| 4 | 3 |

"multi_apps" 名字里的 multi 大多只是 **2 个应用**（72/101，71%）。3 个应用 24 条，4 个应用 3 条。唯一 0 个应用的是标注遗漏（见 A.5），唯一 1 个应用的是 `5bc63fb9`（只标了 `libreoffice_writer`，实际是"读 JSON 文件 → 写 docx"）。

### A.5 已发现的 `related_apps` 标注问题

这些条目在按 `related_apps` 做机器统计时会失真，做实验分桶时需要留意：

| 任务 id | 标注 | 实际情况 |
|---|---|---|
| `2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e` | `[]` 空 | 实际是 Writer 改 docx 参考文献；指令还要求"核实每条文献的真实出版信息"，实质需要联网查证，但 `config` 未预置浏览器 |
| `aceb0368-56b8-4073-b70e-3dc9aee184e0` | `os + thunderbird` | `config` 从不启动 Thunderbird，只解压 `exam.zip` 并打开 Writer + Calc + nautilus，实际是 Writer + Calc + 文件管理器 |
| `e135df7c-7687-4ac0-a5f0-76b74438b53e` | `thunderbird + chrome` | 指令是"把 Calc 里的 xlsx 转成 html 并在 Chrome 里打开"，与 Thunderbird 无关 |
| `3f05f3b9-29ba-4b6b-95aa-2204697ffc06` | `os + picard` | `config` 里有 `chrome_open_tabs`，但任务本身只用 Picard/Kid3 改 MP3 元数据 |
| `2373b66a-092d-44cb-bfd7-82e86e7a3b4d` | `calc + os` | 指令只要求用 `sar` 采样并写 txt，完全没有 Calc |
| `6f4073b8`、`ce2b64a2`、`36037439` 等 | 大小写混用 | `Chrome`/`OS`/`calc`/`writer`/`vscode`/`browser` 与规范名并存，见归一化规则 |

## B. 跨应用交接点是什么

这是本文对本项目最关键的一节。结论先行：

> **文件系统路径是压倒性的第一交接点。** 101 个任务里有 57 个（56%）的跨应用数据流是"应用 A 把文件写到磁盘某个路径 → 应用 B 按路径读回来"（`FS` / `DL` / `UP` / `MAIL` 四类的并集）。判分层面同样：68/101 的 `evaluator` 直接取 `vm_file`（虚拟机上的文件）作为结果。相比之下，**显式剪贴板只有 7 个任务，其中真正跨浏览器/桌面边界的只有 2 个**。

### B.1 交接方式频次

一条任务可有多个标签，因此下表合计大于 101。"主交接"是取每条任务的首要交接方式后的单选统计。

| 交接方式 | 出现任务数 | 作为主交接 | 说明 |
|---|---|---|---|
| `FS` | 41 | 32 | 文件系统路径（磁盘落地，最常见） |
| `W2D` | 18 | 13 | 网页内容 → 誊抄进本地文档 / 表格 |
| `D2W` | 15 | 13 | 本地文件内容 → 驱动浏览器 |
| `NONE` | 11 | 11 | 无真正跨应用数据流 |
| `CLI` | 9 | 7 | 终端命令行驱动 GUI 应用 |
| `DL` | 9 | 7 | 浏览器下载 / 另存 / 打印 PDF → 本地 |
| `MAIL` | 9 | 9 | 邮件客户端 ↔ 文件系统 |
| `UP` | 8 | 3 | 本地文件 → 上传到网页 |
| `CLIP` | 7 | 5 | 显式剪贴板 |
| `SYS` | 3 | 1 | 系统级设置 |

### B.2 按"是否跨浏览器/桌面边界"重新切分

本项目真正关心的不是"跨应用"，而是"跨控制平面"。把 101 个任务按接缝位置重排：

| 接缝类型 | 任务数 | 占 101 |
|---|---|---|
| **纯桌面侧**（两个桌面应用之间，接缝在文件系统 / 剪贴板 / 命令行） | 56 | 55% |
| **跨浏览器 ↔ 桌面边界** | 41 | 41% |
| **浏览器仅背景**（产出与判分全在桌面侧） | 4 | 4% |

再把 41 个跨界任务按接缝介质拆开（前两行有 3 个任务重叠，故 28 + 15 − 3 + 1 = 41）：

| 接缝介质 | 任务数 | 占 41 | 典型任务 |
|---|---|---|---|
| **内容级交接**（不产生文件，靠读取网页内容或读取本地内容再输入浏览器） | 28 | 68% | `236833a3`（HuggingFace 论文列表 → docx）、`c7c1e4c3`（xlsx 里的主页链接 → 查邮箱 → 回填 xlsx） |
| **文件级交接**（文件经磁盘跨界：下载 / 上传） | 15 | 37% | `0e5303d4`（课程网站 PDF → 下载进已打开的文件夹）、`897e3b53`（form.docx → 转 PDF → 传 Google Drive） |
| ├─ 上面两行重叠的部分 | 3 | 7% | `68a25bd4`、`0c825995`、`788b3701` |
| 无数据流（只是分别打开几个东西） | 1 | 2% | `48c46dc7`（同时开终端、文件管理器和两个 Chrome 标签页） |

其中 **显式提到剪贴板的跨界任务只有 2 个**（`f8cfa149` 复制 Calc 的 B6 单元格 → 在 Chrome 搜索；`26660ad1` 复制 speedtest 结果 → 存成 txt），两个都已包含在上表"内容级交接"的 28 个里。

### B.3 五种交接方式的实际画像

#### 1）文件系统路径 —— 最常见，57/101

这是默认交接方式，且**双方都用绝对路径硬编码**，几乎不依赖 GUI 交互本身。典型形态：

- `00fa164e`：从 `~/Documents/awesome-desktop/expe-results.xlsx` 取数 → 插进同目录的 docx。

- `7f35355e`：Calc 把表导成 CSV → VS Code 写脚本读 CSV → 结果写 `result.txt`。

- `881deb30`：读 `~/Documents/Fundings/ecs/ecs15.pdf` … `ecs23.pdf` 九个 PDF → 填进已打开的 xlsx。

- `869de13e`：按内容把桌面上的文件分类移进 `Paper_reading` / `Projects` / `Miscellaneous` 三个目录。

环境本身也在强化这一点：**11 个任务的 `config` 会预先用 `nautilus` 打开目标文件夹**，等于把"交接点在哪个目录"直接摆在屏幕上。

#### 2）浏览器下载 → 本地应用打开 —— 9 个（8 个需要浏览器）

| 任务 id | 形态 |
|---|---|
| `0e5303d4` | 课程网站逐周 PDF → 下载进 `~/lecture_slides`（`config` 已用 nautilus 打开该目录） |
| `74d5859f` | webext.eu 在线生成扩展骨架 → 下载 zip → 解压到 `~/Projects` |
| `788b3701` | 先读本地小说目录判断"下一章是哪章" → 再去 GitHub 下载该章 |
| `da922383` | 把当前打开的博客标签页逐个"打印为 PDF" → 按标题存到 `~/Documents/Blog` |
| `e1fc0df3` | 从网上下载 LanguageTool 的 `.oxt` → 用 LibreOffice 扩展管理器安装 |
| `f8369178` | 从 gnome-look.org 下载 Orchis 主题 → 安装并切换 GNOME 主题 |
| `42d25c08` | 本地 txt 小说上传到在线转换工具 → 下载 epub（上下行都有） |
| `68a25bd4` | 表格里的链接 → 下载论文 PDF 存为 `paper01.pdf` |
| `3c8f201a` | 给定 URL 下载图片 → GIMP 压缩（`config` 只开终端，实际走 `wget` 即可，未标 chrome） |
注意 `da922383` 和 chrome domain 的 `e1e75309`：**"把网页存成 PDF"这个动作横跨两侧**——触发在浏览器，但落点、文件名和保存对话框在桌面侧。

#### 3）从网页复制内容 → 粘贴到文档 —— 18 个（`W2D`，最大的跨界类别）

这类任务不产生中间文件，agent 必须"看懂网页 → 记住 → 在桌面应用里重新输入"。典型：

- `236833a3`：HuggingFace 2024-03-01 的每日论文列表 → 按已有两条的格式补全 docx。

- `3e3fc409`：IMDB Top 30 → 与本地已看电影表对比 → 新建 `unseen_movies` sheet。

- `67890eb6`：ACL 2019–2022 最佳长论文的标题/年份/作者/PDF 链接 → 写进 xlsx。

- `aad10cd7`：Apple HIG 的一段文档 → 存成 `notes.docx`。

- `dd60633f`：Karpathy 的 Colab 代码单元 → 合并成一个 `.py`。

值得注意：**这 18 个里没有一个在指令中提到剪贴板**。OSWorld 只约束最终文件内容，不约束搬运手段——所以内容可以走剪贴板，也可以走"读出来再打字"，甚至可以完全绕过 GUI 用脚本写文件。

#### 4）读取本地文件 → 填进网页 / 驱动浏览器 —— 15 个（`D2W`）

这是与 3）方向相反的一类，也是本项目接缝设计上更容易被忽略的一类：**浏览器控制平面必须先拿到桌面侧的内容才能开始工作**。

| 任务 id | 桌面侧提供什么 | 浏览器侧做什么 |
|---|---|---|
| `c7c1e4c3` | xlsx 里的一列教授主页 URL | 逐个打开抓邮箱，再回填同一个 xlsx（双向） |
| `d1acdb87` | `restaurants.txt` 的餐厅名单 | Google Maps 查地址/网站/电话，回填 xlsx（双向） |
| `df67aebb` | docx 里的参考文献列表 | dblp 查 BibTeX，写进 `references.bib`（双向） |
| `873cafdd` | docx 里的插件推荐名单 | Chrome 应用商店逐个安装 |
| `58565672` | Thunderbird 邮件正文里的第一个链接 | 在新标签页打开 |
| `36037439` | 本地 PDF 里的通讯作者姓名 | 打开该作者的 Google Scholar 页 |
| `a82b78bb` | 本地 PDF 的首位和末三位作者 | 找个人主页并存进书签文件夹 |
| `a74b607e` | Desktop 上解压好的扩展目录路径 | `chrome://extensions` 加载已解压扩展（要走原生文件选择器） |
| `e135df7c` | Calc 导出的本地 html 文件 | 用 `file://` 在 Chrome 打开 |
| `f8cfa149` | Calc 单元格 B6 的值 | 在 Chrome 里搜这个值 |
其中 5 个是**双向往返**（`68a25bd4`、`c7c1e4c3`、`d1acdb87`、`da52d699`、`df67aebb`）：本地 → 网页 → 再回本地。这类任务两个控制平面必须在同一次会话里反复交替。

#### 5）剪贴板 —— 只有 7 个，跨界的只有 2 个

| 任务 id | 剪贴板角色 | 是否跨浏览器边界 |
|---|---|---|
| `f8cfa149` | Calc 单元格 → Chrome 搜索框 | 是 |
| `26660ad1` | speedtest.net 结果 → 本地 txt | 是 |
| `00fa164e` | Calc 区域 → Writer 表格（要求保留四位小数等显示格式） | 否，Calc→Writer |
| `81c425f5` | Calc 区域 → Writer 表格（要求保留原格式） | 否，Calc→Writer |
| `227d2f97` | GIMP 图像 → Writer 文档 | 否，GIMP→Writer |
| `5bc63fb9` | JSON 内容 → docx 段落 | 否，同为本地文件 |
| `716a6079` | 剪贴板本身就是判分目标（把 `secret.docx` 的路径复制到剪贴板，判分函数 `is_in_vm_clickboard`） | 否 |
结论：**剪贴板不是 OSWorld 的主要交接机制**。它主要出现在"必须保留富文本/图像格式"的 LibreOffice 应用间搬运（`00fa164e`、`81c425f5`、`227d2f97`），这三个用剪贴板是因为格式保真，而不是因为传数据。

### B.4 另外三种非典型交接

- **邮件客户端 ↔ 文件系统（`MAIL`，9 个）**：Thunderbird 的附件另存、邮件导出 `.eml`、通讯录导出 CSV。其中 4 个（`46407397`、`78aed49a`、`a0b9dc9c`、`b52b40a5`）是"Thunderbird → 本地文件 → 上传 Google Drive"的三段式，桌面和浏览器各占一头，中间还是文件系统。10 个任务的 `config` 会解包 `thunderbird-profile.tar.gz` 来预置邮箱状态。

- **终端命令行驱动 GUI 应用（`CLI`，9 个）**：`f7dfbef3`（`soffice --convert-to pdf` 批量转换）、`ee9a3c83`（LibreOffice 实例运行时用命令行转 ods→csv）、`510f64c8`（`code ~/Desktop/project`）、`91190194`（命令行启 GIMP）、`2b9493d7`（命令行强杀 Writer）。这类任务的判分会读 `vm_command_line`（14 个任务用到），即**检查 bash 历史里执行了什么命令**，不只看结果文件。6 个任务的 `config` 预开 `gnome-terminal`。

- **系统级设置（`SYS`，3 个）**：`937087b6`（设 VLC 为默认播放器）、`c2751594`（邮件附件里的图 → 设为桌面壁纸）、`f8369178`（装 GNOME 主题）。

### B.5 对本项目的直接含义

1. **接缝的默认协议应该是文件路径，而不是剪贴板。** 两个控制平面之间最该打通的原语是"共享文件系统视图 + 一个约定的落地目录"，而不是同步剪贴板。剪贴板只在 7/101 出现，跨界只有 2 个。

2. **但内容级交接（28/41）才是跨界任务的主流，且它不经过文件系统。** 浏览器侧读到的网页内容需要以结构化文本形式交回编排层，再由 AT-SPI 侧输入桌面应用。这条通路必须能承载表格、多行文本和精确格式（`00fa164e` 明确要求"包括尾随零和四位小数"）。

3. **方向是双向的，且 5 个任务要求同一会话内反复往返。** 不能设计成"先浏览器跑完再交给桌面"的单向流水线。

4. **有三类动作天然横跨两侧，需要专门设计**：浏览器下载的落点与文件名、"打印为 PDF"的保存对话框、`chrome://extensions` 加载已解压扩展时弹出的**原生 GTK 文件选择器**（`a74b607e`、chrome domain 的 `6766f2b8`）。这些对话框不在网页 DOM 里，Playwright 抓不到，必须由 AT-SPI 侧接管。

5. **判分侧也横跨两个平面**，见 C.3。


## C. chrome domain 里有多少任务其实只需要浏览器

46 个任务全部标注为 `related_apps: ["chrome"]`，但按"是否必须动到桌面侧"重新判读后分成四类：

| 归类 | 任务数 | 占 46 | 能否整个交给浏览器控制平面 |
|---|---|---|---|
| **纯网页操作** | 26 | 57% | 可以，Playwright / browser-use 全权处理 |
| **浏览器自身 UI**（`chrome://` 设置、书签、历史、profile） | 14 | 30% | 概念上在浏览器内，但不是网页 DOM，见 C.2 |
| **需桌面侧配合** | 3 | 7% | 不行，见 C.3 |
| **infeasible**（官方标为不可完成） | 3 | 7% | 只需判断"做不到"，无 GUI 动作 |

**直接回答：完全不触碰桌面应用的有 40 个（26 + 14，占 87%）；真正需要桌面侧配合的只有 3 个。**

但这 40 个里只有 **26 个是普通网页操作**，另外 14 个要操作浏览器自己的设置界面，工程上是另一回事。

### C.1 纯网页操作（26 个）

全是"在某个站点上导航 / 搜索 / 筛选 / 排序 / 填表"，判分方式是检查最终 URL、URL 参数或页面正文：

- 机票酒店租车：`6c4c23a1`(Delta)、`fc6d8143`(Delta)、`82bc8d6a`(Qatar)、`f79439ad`(Ryanair)、`1704f00f`(rentalcars)、`47543840`(Budget)、`b7895e80`(TripAdvisor)、`c1fa57f3`(United 行李费计算器)

- 电商筛选：`2888b4e6`(Macy's)、`7f52cab9`(Google Shopping)、`9f3f70fc`(NBA Store)、`cabb3bae`(Kohl's)、`121ba48f`(Steam 加购 DLC)、`82279c77`(cars.com)、`f5d96daf`(Apple 机型对比)

- 信息查找：`0d8b7de3`/`b070486d`(drugs.com)、`368d9ba4`(AccuWeather)、`59155008`(BabyCenter)、`9f935cce`(justice.gov)、`a728a36e`(Virginia DMV)、`a96b564e`(FlightAware 论坛)、`b4f95342`(recreation.gov)、`f0b971a1`(NFL)、`f3b19d1e`(Ticketek FAQ)、`da46d875`(MBTA 预约表单填写)

这 26 个是本项目"把浏览器整个交出去"最干净的验证集。

### C.2 浏览器自身 UI（14 个）—— 不触桌面，但也不是网页

| 任务 id | 目标 |
|---|---|
| `030eeff7-b492-4218-b312-701ec99ee0cc` | chrome://settings/privacy 开启 Do Not Track |
| `06fe7178-4491-4589-810f-2e2bc9502122` | 恢复最近关闭的标签页(Ctrl+Shift+T / 历史菜单) |
| `12086550-11c0-466b-b367-1d9e75b3910e` | chrome://settings/passwords |
| `2ad9387a-65d8-4e33-ad5b-7580065a27ca` | 书签栏新建文件夹(书签管理器) |
| `2ae9ba84-3a0d-4d4c-8338-3a1478dc5fe3` | chrome://settings 修改 profile 用户名 |
| `3299584d-8f11-4457-bf4c-ce98f7600250` | chrome://settings/onStartup 去掉启动页 |
| `44ee5668-ecd5-4366-a6ce-c1c9b8d4e938` | chrome://history 按站点清除历史 |
| `7a5a7856-f1b6-42a4-ade9-1ca81ca0f263` | 把当前页加入书签栏 |
| `7b6c7e24-c58a-49fc-a5bb-d57b80e5b4c3` | 清除 amazon 的 cookie/站点数据 |
| `93eabf48-6a27-4cb6-b963-7d5fe1e0d3a9` | chrome://settings/appearance 关闭深色模式 |
| `9656a811-9b5b-4ddf-99c7-5117bcef0626` | chrome://settings/security 开启安全浏览 |
| `99146c54-4f37-4ab8-9327-5f3291665e1e` | 关闭时自动清除浏览数据 |
| `af630914-714e-4a24-a7bb-f9af687d3b91` | chrome://settings/appearance 字号调到最大 |
| `bb5e4c0d-f964-439c-97b6-bdb9747de3f4` | 默认搜索引擎改 Bing |

这批任务的共同点：目标不在任何网页的 DOM 里，而在 `chrome://settings` / `chrome://history` / `chrome://extensions` / 书签管理器这类 WebUI 页面，或者干脆是浏览器菜单和快捷键（`06fe7178` 是 Ctrl+Shift+T 恢复最近关闭的标签页）。

工程含义：这些 WebUI 页面重度使用 shadow DOM，且 Playwright 的 `page.goto("chrome://…")` 通常被拒；即使通过 CDP 建立 target，选择器也极难写。**这 14 个任务是"浏览器控制平面"和"AT-SPI 桌面平面"职责边界最模糊的地带**，本项目需要显式决定它们归谁，不能默认落在网页侧。

### C.3 需要桌面侧配合的 3 个

| 任务 id | 指令 | 需要桌面侧做什么 |
|---|---|---|
| `35253b65-1c19-4304-8aa4-6884b8218fc0` | 用 Chrome 内建功能给当前站点在桌面上建快捷方式 | 走 Chrome 三点菜单（原生 UI），产物是 `~/Desktop/*.desktop` 文件；判分函数 `get_shortcuts_on_desktop` 直接读 Desktop 目录树 |
| `6766f2b8-8a72-417f-a9e5-56fcaa735837` | 把 Desktop 上下载好的扩展 zip 解压，并在 Chrome 扩展页里配置 | ① 文件系统解压；② `chrome://extensions` 打开开发者模式后"加载已解压的扩展程序"会弹**原生 GTK 文件选择器**，必须由桌面侧选目录 |
| `e1e75309-3ddb-4d09-92ec-de869c928143` | 把当前网页存成 PDF 到 Desktop，用默认文件名，边距设为无 | 打印预览里改边距 + 原生保存对话框 + 落盘到 Desktop；判分下载参考 PDF 后与 `~/Desktop` 下的产物比对 |

三个都是同一个模式：**动作从浏览器发起，但落点在文件系统，中间隔着一个非 DOM 的原生对话框。** 这与 B.5 第 4 点完全一致。

### C.4 附：chrome domain 的判分本身就跨两个平面

这一点对本项目同样重要——即使任务"只需要浏览器"，OSWorld 的**判分**也未必只走浏览器：

| 判分通道 | 任务数 | 实现 |
|---|---|---|
| CDP / Playwright 读页面 | 26 | `connect_over_cdp` 后读 tab 列表、URL、页面 HTML、正文 |
| 读 VM 上的 Chrome 配置文件 | 12 | 直接读 `~/.config/google-chrome/Default/Preferences` 或 `Bookmarks`、`History` 的 JSON |
| **AT-SPI 无障碍树读地址栏** | 10 | `get_active_url_from_accessTree` 用 CSS 选择器 `application[name=Google\ Chrome] entry[name=Address\ and\ search\ bar]` 从桌面无障碍树里取当前 URL |
| 读 VM 文件系统产物 | 2 | Desktop 上的 `.desktop` / `.pdf` |
两个细节值得记下来：

- OSWorld 的注释明说 *"Playwright cannot get the url of active tab directly, so we need to use accessibility tree"*——**连官方判分器都要靠 AT-SPI 才能知道"当前哪个标签页是活动的"**。这正是本项目双平面架构必须解决的同一个问题：CDP 能看到所有 target，但看不到"用户此刻在看哪一个"。

- 12 个读配置文件的任务里有 9 个在 `postconfig` 里先 `pkill chrome` 再重启 Chrome——因为 Chrome 的 preferences 是延迟落盘的。若本项目自己管理浏览器进程，需要注意"设置类改动何时对外可见"。

- 44/46 的 `config` 用 `google-chrome --remote-debugging-port=1337` + `socat` 转发到 9222 启动浏览器，31/46 会用 `chrome_open_tabs` 预置起始标签页。**环境默认就假定浏览器是被 CDP 接管的**，这与本项目的方向一致。


## D. 有多少 multi_apps 任务完全不涉及浏览器

**56 个（55%）完全不涉及浏览器**，是纯桌面应用 / 终端 / 文件系统的组合。

另有 **4 个"浏览器仅背景"**：`config` 启了 Chrome 或 `related_apps` 标了 chrome，但产出和判分完全在桌面侧。把这 4 个也算上，**60 个（59%）任务的可交付物和判分完全落在桌面侧**，只有 41 个（41%）真正需要浏览器控制平面参与。

这四个"仅背景"的任务是：

| 任务 id | 情况 |
|---|---|
| `3f05f3b9-29ba-4b6b-95aa-2204697ffc06` | `config` 有 `chrome_open_tabs`，任务是用 Picard/Kid3 补 MP3 元数据 |
| `48d05431-6cd5-4e76-82eb-12b60d823f7d` | 修 `conda: command not found`，判分读 `vm_command_line`，浏览器只是用来查解法 |
| `acb0f96b-e27c-44d8-b55f-7cb76609dfcd` | `git clone` 一个 GitHub 仓库到 `/home/user`，判分比对 `ls -R` 输出，实际是终端任务 |
| `e2392362-125e-4f76-a2ee-524b183a3412` | 按在线教程改本地 `_config.yml`，判分只读该文件 |

### D.1 这 56 个纯桌面任务的应用组合

| 组合 | 任务数 |
|---|---|
| libreoffice_calc + os | 5 |
| libreoffice_writer + os | 4 |
| gimp + os | 3 |
| os + vs_code | 3 |
| gimp + libreoffice_writer + os | 2 |
| gimp + vs_code | 2 |
| libreoffice_calc + libreoffice_writer + os | 2 |
| libreoffice_calc + os + thunderbird | 2 |
| libreoffice_calc + terminal | 2 |
| libreoffice_calc + thunderbird | 2 |
| libreoffice_impress + libreoffice_writer | 2 |
| libreoffice_impress + vlc | 2 |
| libreoffice_writer + terminal | 2 |
| os + terminal | 2 |
| os + thunderbird | 2 |
| (空标注) | 1 |
| gimp + libreoffice_impress + os | 1 |
| gimp + os + pdf | 1 |
| gimp + vlc | 1 |
| image + libreoffice_calc + os + pdf | 1 |
| image + os | 1 |
| libreoffice_calc + libreoffice_impress + libreoffice_writer + os | 1 |
| libreoffice_calc + libreoffice_impress + libreoffice_writer + vlc | 1 |
| libreoffice_calc + libreoffice_writer | 1 |
| libreoffice_calc + os + pdf | 1 |
| libreoffice_calc + vs_code | 1 |
| libreoffice_impress + libreoffice_writer + os | 1 |
| libreoffice_writer | 1 |
| libreoffice_writer + os + vs_code | 1 |
| libreoffice_writer + vs_code | 1 |
| os + pdf | 1 |
| os + ubuntu_media_player + vlc | 1 |
| os + vlc | 1 |
| terminal + vs_code | 1 |

### D.2 这 56 个任务的交接方式分布

| 交接方式 | 任务数 |
|---|---|
| `FS` | 39 |
| `CLI` | 9 |
| `NONE` | 7 |
| `CLIP` | 5 |
| `MAIL` | 5 |
| `SYS` | 2 |
| `DL` | 1 |

可以看到纯桌面侧几乎只有一种交接方式：**文件系统（39/56，70%）**。其次是命令行驱动 GUI（9）、邮件客户端导出（5）、剪贴板（5）。

### D.3 对本项目的含义

1. **AT-SPI 桌面平面要独立扛住 55%–59% 的 multi_apps 任务**，这些任务里浏览器控制平面完全用不上。桌面侧的能力上限直接决定 multi_apps 的成绩上限。

2. 桌面侧最需要打磨的不是"跨应用通信"，而是**同一台机器上多个 GUI 应用 + 文件系统的稳定编排**：按窗口标题切换应用、把文档保存到指定路径、在文件管理器里移动/重命名/压缩。

3. 环境和判分都在强化"窗口标题 + 保存"这条链路：**23 个 multi_apps 任务的 `postconfig` 会先 `activate_window`（按窗口标题精确匹配，如 `"awe_desk_env.docx - LibreOffice Writer"`）再用 pyautogui 发 Ctrl+S 强制保存**，然后才取文件比对。也就是说，如果 agent 让窗口标题偏离预期（比如另存成了别的文件名），判分会直接失败。桌面平面必须能可靠地按标题定位窗口。

4. **14 个任务的判分读 `vm_command_line`（bash 历史）**，即检查"你到底执行了哪条命令"，而不只是结果。终端也是一个需要被观测的一等公民。


## 附录 A：multi_apps 全量标注表（101 条）

"浏览器"列：必需 / 仅背景 / 不涉及。"交接方式"列代号见开头口径定义。

| # | 任务 id | related_apps (归一化) | 浏览器 | 交接方式 |
|---|---|---|---|---|
| 1 | `00fa164e-2612-4439-992e-157d019a8436` | libreoffice_calc+libreoffice_writer+os | 不涉及 | CLIP, FS |
| 2 | `02ce9a50-7af2-47ed-8596-af0c230501f8` | libreoffice_writer+os | 不涉及 | FS, CLI |
| 3 | `09a37c51-e625-49f4-a514-20a773797a8a` | gimp+libreoffice_writer+os | 不涉及 | FS |
| 4 | `0c825995-5b70-4526-b663-113f4c999dd2` | chrome+libreoffice_calc+os | 必需 | D2W, UP |
| 5 | `0e5303d4-8820-42f6-b18d-daf7e633de21` | chrome+os | 必需 | DL |
| 6 | `185f29bd-5da0-40a6-b69c-ba7f4e0324ef` | libreoffice_calc+os+pdf | 不涉及 | FS |
| 7 | `1f18aa87-af6f-41ef-9853-cdb8f32ebdea` | libreoffice_writer+os | 不涉及 | FS |
| 8 | `20236825-b5df-46e7-89bf-62e1d640a897` | libreoffice_writer+os+vs_code | 不涉及 | FS |
| 9 | `227d2f97-562b-4ccb-ae47-a5ec9e142fbb` | gimp+libreoffice_writer+os | 不涉及 | CLIP |
| 10 | `22a4636f-8179-4357-8e87-d1743ece1f81` | chrome+libreoffice_writer | 必需 | UP |
| 11 | `236833a3-5704-47fc-888c-4f298f09f799` | chrome+libreoffice_writer | 必需 | W2D |
| 12 | `2373b66a-092d-44cb-bfd7-82e86e7a3b4d` | libreoffice_calc+os | 不涉及 | NONE |
| 13 | `26150609-0da3-4a7d-8868-0faf9c5f01bb` | os+vs_code | 不涉及 | NONE |
| 14 | `26660ad1-6ebb-4f59-8cba-a8432dfe8d38` | chrome+os | 必需 | W2D, CLIP |
| 15 | `2b9493d7-49b8-493a-a71b-56cd1f4d6908` | libreoffice_writer+terminal | 不涉及 | CLI |
| 16 | `2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e` | (空) | 不涉及 | FS |
| 17 | `2c9fc0de-3ee7-45e1-a5df-c86206ad78b5` | os+terminal | 不涉及 | NONE |
| 18 | `2fe4b718-3bd7-46ec-bdce-b184f5653624` | gimp+vlc | 不涉及 | FS |
| 19 | `337d318b-aa07-4f4f-b763-89d9a2dd013f` | os+pdf | 不涉及 | FS |
| 20 | `36037439-2044-4b50-b9d1-875b5a332143` | chrome+os | 必需 | D2W |
| 21 | `3680a5ee-6870-426a-a997-eba929a0d25c` | libreoffice_calc+terminal | 不涉及 | CLI, FS |
| 22 | `3a93cae4-ad3e-403e-8c12-65303b271818` | libreoffice_calc+os | 不涉及 | NONE |
| 23 | `3c8f201a-009d-4bbe-8b65-a6f8b35bb57f` | gimp+os | 不涉及 | DL, FS |
| 24 | `3e3fc409-bff3-4905-bf16-c968eee3f807` | chrome+libreoffice_calc | 必需 | W2D |
| 25 | `3f05f3b9-29ba-4b6b-95aa-2204697ffc06` | os+picard | 仅背景 | FS |
| 26 | `415ef462-bed3-493a-ac36-ca8c6d23bf1b` | libreoffice_calc+os+thunderbird | 不涉及 | MAIL, FS |
| 27 | `42d25c08-fb87-4927-8b65-93631280a26f` | chrome+os | 必需 | UP, DL |
| 28 | `42f4d1c7-4521-4161-b646-0a8934e36081` | gimp+vs_code | 不涉及 | FS, CLI |
| 29 | `46407397-a7d5-4c6b-92c6-dbe038b1457b` | chrome+thunderbird | 必需 | MAIL, UP |
| 30 | `47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5` | libreoffice_impress+vlc | 不涉及 | FS |
| 31 | `48c46dc7-fe04-4505-ade7-723cba1aa6f6` | chrome+os | 必需 | NONE |
| 32 | `48d05431-6cd5-4e76-82eb-12b60d823f7d` | chrome+os | 仅背景 | NONE |
| 33 | `4c26e3f3-3a14-4d86-b44a-d3cedebbb487` | gimp+libreoffice_impress+os | 不涉及 | FS |
| 34 | `4e9f0faf-2ecc-4ae8-a804-28c9a75d1ddc` | chrome+libreoffice_calc | 必需 | W2D |
| 35 | `510f64c8-9bcc-4be1-8d30-638705850618` | terminal+vs_code | 不涉及 | CLI |
| 36 | `51f5801c-18b3-4f25-b0c3-02f85507a078` | libreoffice_impress+libreoffice_writer | 不涉及 | FS |
| 37 | `58565672-7bfe-48ab-b828-db349231de6b` | chrome+thunderbird | 必需 | D2W |
| 38 | `5990457f-2adb-467b-a4af-5c857c92d762` | chrome+libreoffice_calc | 必需 | W2D |
| 39 | `5bc63fb9-276a-4439-a7c1-9dc76401737f` | libreoffice_writer | 不涉及 | FS, CLIP |
| 40 | `5df7b33a-9f77-4101-823e-02f863e1c1ae` | libreoffice_writer+os | 不涉及 | FS |
| 41 | `67890eb6-6ce5-4c00-9e3d-fb4972699b06` | chrome+libreoffice_calc | 必需 | W2D |
| 42 | `68a25bd4-59c7-4f4d-975e-da0c8509c848` | chrome+libreoffice_calc | 必需 | D2W, DL, W2D |
| 43 | `69acbb55-d945-4927-a87b-8480e1a5bb7e` | chrome+os+vs_code | 必需 | W2D |
| 44 | `6d72aad6-187a-4392-a4c4-ed87269c51cf` | libreoffice_calc+libreoffice_impress+libreoffice_writer+vlc | 不涉及 | NONE |
| 45 | `6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a` | chrome+libreoffice_calc+os | 必需 | W2D |
| 46 | `716a6079-22da-47f1-ba73-c9d58f986a38` | os+terminal | 不涉及 | CLIP |
| 47 | `74d5859f-ed66-4d3e-aa0e-93d7a592ce41` | chrome+os | 必需 | DL |
| 48 | `778efd0a-153f-4842-9214-f05fc176b877` | libreoffice_impress+vlc | 不涉及 | FS |
| 49 | `788b3701-3ec9-4b67-b679-418bfa726c22` | chrome+os | 必需 | DL, D2W |
| 50 | `78aed49a-a710-4321-a793-b611a7c5b56b` | chrome+thunderbird | 必需 | MAIL, UP |
| 51 | `7e287123-70ca-47b9-8521-47db09b69b14` | libreoffice_calc+os | 不涉及 | FS |
| 52 | `7f35355e-02a6-45b5-b140-f0be698bcf85` | libreoffice_calc+vs_code | 不涉及 | FS |
| 53 | `7ff48d5b-2df2-49da-b500-a5150ffc7f18` | chrome+libreoffice_writer+os | 必需 | W2D |
| 54 | `81c425f5-78f3-4771-afd6-3d2973825947` | libreoffice_calc+libreoffice_writer+os | 不涉及 | CLIP, FS |
| 55 | `82e3c869-49f6-4305-a7ce-f3e64a0618e7` | image+os | 不涉及 | FS |
| 56 | `869de13e-bef9-4b91-ba51-f6708c40b096` | libreoffice_calc+libreoffice_impress+libreoffice_writer+os | 不涉及 | FS |
| 57 | `873cafdd-a581-47f6-8b33-b9696ddb7b05` | chrome+libreoffice_writer+os | 必需 | D2W |
| 58 | `881deb30-9549-4583-a841-8270c65f2a17` | libreoffice_calc+os | 不涉及 | FS |
| 59 | `897e3b53-5d4d-444b-85cb-2cdc8a97d903` | chrome+libreoffice_writer | 必需 | UP |
| 60 | `8df7e444-8e06-4f93-8a1a-c5c974269d82` | libreoffice_writer+os | 不涉及 | FS |
| 61 | `8e116af7-7db7-4e35-a68b-b0939c066c78` | image+libreoffice_calc+os+pdf | 不涉及 | FS |
| 62 | `91190194-f406-4cd6-b3f9-c43fac942b22` | gimp+os | 不涉及 | CLI |
| 63 | `9219480b-3aed-47fc-8bac-d2cffc5849f7` | os+vs_code | 不涉及 | NONE |
| 64 | `937087b6-f668-4ba6-9110-60682ee33441` | os+vlc | 不涉及 | SYS |
| 65 | `98e8e339-5f91-4ed2-b2b2-12647cb134f4` | libreoffice_writer+vs_code | 不涉及 | FS |
| 66 | `9f3bb592-209d-43bc-bb47-d77d9df56504` | os+ubuntu_media_player+vlc | 不涉及 | FS |
| 67 | `a0b9dc9c-fc07-4a88-8c5d-5e3ecad91bcb` | chrome+thunderbird | 必需 | MAIL, UP |
| 68 | `a503b07f-9119-456b-b75d-f5146737d24f` | gimp+os+pdf | 不涉及 | FS |
| 69 | `a74b607e-6bb5-4ea8-8a7c-5d97c7bbcd2a` | chrome+os | 必需 | D2W |
| 70 | `a82b78bb-7fde-4cb3-94a4-035baf10bcf0` | chrome+pdf | 必需 | D2W |
| 71 | `aad10cd7-9337-4b62-b704-a857848cedf2` | chrome+os | 必需 | W2D |
| 72 | `acb0f96b-e27c-44d8-b55f-7cb76609dfcd` | chrome+os | 仅背景 | NONE |
| 73 | `aceb0368-56b8-4073-b70e-3dc9aee184e0` | os+thunderbird | 不涉及 | FS |
| 74 | `b337d106-053f-4d37-8da0-7f9c4043a66b` | chrome+os | 必需 | W2D |
| 75 | `b5062e3e-641c-4e3a-907b-ac864d2e7652` | libreoffice_calc+os | 不涉及 | FS |
| 76 | `b52b40a5-ad70-4c53-b5b0-5650a8387052` | chrome+thunderbird | 必需 | MAIL, UP |
| 77 | `bb83cab4-e5c7-42c7-a67b-e46068032b86` | libreoffice_impress+libreoffice_writer+os | 不涉及 | FS |
| 78 | `bc2b57f3-686d-4ec9-87ce-edf850b7e442` | libreoffice_calc+libreoffice_writer | 不涉及 | FS |
| 79 | `c2751594-0cd5-4088-be1b-b5f2f9ec97c4` | os+thunderbird | 不涉及 | MAIL, SYS |
| 80 | `c7c1e4c3-9e92-4eba-a4b8-689953975ea4` | chrome+libreoffice_calc | 必需 | D2W, W2D |
| 81 | `c867c42d-a52d-4a24-8ae3-f75d256b5618` | libreoffice_calc+thunderbird | 不涉及 | MAIL, FS |
| 82 | `ce2b64a2-ddc1-4f91-8c7d-a88be7121aac` | chrome+image+os | 必需 | D2W, FS |
| 83 | `d1acdb87-bb67-4f30-84aa-990e56a09c92` | chrome+libreoffice_calc+os | 必需 | D2W, W2D |
| 84 | `d68204bf-11c1-4b13-b48b-d303c73d4bf6` | gimp+os | 不涉及 | CLI |
| 85 | `d9b7c649-c975-4f53-88f5-940b29c47247` | libreoffice_calc+thunderbird | 不涉及 | MAIL, FS |
| 86 | `da52d699-e8d2-4dc5-9191-a2199e0b6a9b` | chrome+libreoffice_calc+libreoffice_writer | 必需 | D2W, W2D |
| 87 | `da922383-bfa4-4cd3-bbad-6bebab3d7742` | chrome+os | 必需 | DL |
| 88 | `dd60633f-2c72-42ba-8547-6f2c8cb0fdb0` | chrome+libreoffice_writer+os | 必需 | W2D |
| 89 | `deec51c9-3b1e-4b9e-993c-4776f20e8bb2` | chrome+libreoffice_calc+os | 必需 | W2D |
| 90 | `df67aebb-fb3a-44fd-b75b-51b6012df509` | chrome+libreoffice_writer | 必需 | D2W, W2D |
| 91 | `e135df7c-7687-4ac0-a5f0-76b74438b53e` | chrome+thunderbird | 必需 | D2W |
| 92 | `e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56` | chrome+libreoffice+os | 必需 | DL |
| 93 | `e2392362-125e-4f76-a2ee-524b183a3412` | chrome+os+vs_code | 仅背景 | NONE |
| 94 | `e8172110-ec08-421b-a6f5-842e6451911f` | gimp+vs_code | 不涉及 | FS |
| 95 | `eb303e01-261e-4972-8c07-c9b4e7a4922a` | libreoffice_impress+libreoffice_writer | 不涉及 | FS |
| 96 | `ee9a3c83-f437-4879-8918-be5efbb9fac7` | libreoffice_calc+terminal | 不涉及 | CLI |
| 97 | `f5c13cdd-205c-4719-a562-348ae5cd1d91` | libreoffice_calc+os+thunderbird | 不涉及 | MAIL, FS |
| 98 | `f7dfbef3-7697-431c-883a-db8583a4e4f9` | libreoffice_writer+terminal | 不涉及 | CLI |
| 99 | `f8369178-fafe-40c2-adc4-b9b08a125456` | chrome+os | 必需 | DL, SYS |
| 100 | `f8cfa149-d1c1-4215-8dac-4a0932bad3c2` | chrome+libreoffice_calc | 必需 | CLIP, D2W |
| 101 | `f918266a-b3e0-4914-865d-4faa564f1aef` | os+vs_code | 不涉及 | NONE |

## 附录 B：chrome 全量标注表（46 条）

| # | 任务 id | 归类 | 说明 |
|---|---|---|---|
| 1 | `030eeff7-b492-4218-b312-701ec99ee0cc` | 浏览器自身 UI | chrome://settings/privacy 开启 Do Not Track |
| 2 | `06fe7178-4491-4589-810f-2e2bc9502122` | 浏览器自身 UI | 恢复最近关闭的标签页(Ctrl+Shift+T / 历史菜单) |
| 3 | `0d8b7de3-e8de-4d86-b9fd-dd2dce58a217` | 纯网页 | drugs.com 导航 |
| 4 | `12086550-11c0-466b-b367-1d9e75b3910e` | 浏览器自身 UI | chrome://settings/passwords |
| 5 | `121ba48f-9e17-48ce-9bc6-a4fb17a7ebba` | 纯网页 | Steam 加购 DLC |
| 6 | `1704f00f-79e6-43a7-961b-cedd3724d5fd` | 纯网页 | rentalcars.com 搜索筛选 |
| 7 | `2888b4e6-5b47-4b57-8bf5-c73827890774` | 纯网页 | macys.com 筛选 |
| 8 | `2ad9387a-65d8-4e33-ad5b-7580065a27ca` | 浏览器自身 UI | 书签栏新建文件夹(书签管理器) |
| 9 | `2ae9ba84-3a0d-4d4c-8338-3a1478dc5fe3` | 浏览器自身 UI | chrome://settings 修改 profile 用户名 |
| 10 | `3299584d-8f11-4457-bf4c-ce98f7600250` | 浏览器自身 UI | chrome://settings/onStartup 去掉启动页 |
| 11 | `35253b65-1c19-4304-8aa4-6884b8218fc0` | 需桌面侧 | Chrome"创建快捷方式" -> 桌面 .desktop 文件, 判分读 Desktop 目录树 |
| 12 | `368d9ba4-203c-40c1-9fa3-da2f1430ce63` | 纯网页 | accuweather 月度预报 |
| 13 | `3720f614-37fd-4d04-8a6b-76f54f8c222d` | infeasible | 把 Chrome 界面语言改成虚构语言 |
| 14 | `44ee5668-ecd5-4366-a6ce-c1c9b8d4e938` | 浏览器自身 UI | chrome://history 按站点清除历史 |
| 15 | `47543840-672a-467d-80df-8f7c3b9788c9` | 纯网页 | budget.com 租车筛选 |
| 16 | `480bcfea-d68f-4aaa-a0a9-2589ef319381` | infeasible | 关闭 2023 版 Chrome UI |
| 17 | `59155008-fe71-45ec-8a8f-dc35497b6aa8` | 纯网页 | babycenter 相似名字 |
| 18 | `6766f2b8-8a72-417f-a9e5-56fcaa735837` | 需桌面侧 | 先在文件系统解压 zip, 再 chrome://extensions 载入已解压扩展(原生文件选择器) |
| 19 | `6c4c23a1-42a4-43cc-9db1-2f86ff3738cc` | 纯网页 | delta.com 航班里程票 |
| 20 | `7a5a7856-f1b6-42a4-ade9-1ca81ca0f263` | 浏览器自身 UI | 把当前页加入书签栏 |
| 21 | `7b6c7e24-c58a-49fc-a5bb-d57b80e5b4c3` | 浏览器自身 UI | 清除 amazon 的 cookie/站点数据 |
| 22 | `7f52cab9-535c-4835-ac8c-391ee64dc930` | 纯网页 | Google Shopping 筛选 |
| 23 | `82279c77-8fc6-46f6-9622-3ba96f61b477` | 纯网页 | cars.com 电车筛选 |
| 24 | `82bc8d6a-36eb-4d2d-8801-ef714fb1e55a` | 纯网页 | qatarairways 查航班 |
| 25 | `93eabf48-6a27-4cb6-b963-7d5fe1e0d3a9` | 浏览器自身 UI | chrome://settings/appearance 关闭深色模式 |
| 26 | `9656a811-9b5b-4ddf-99c7-5117bcef0626` | 浏览器自身 UI | chrome://settings/security 开启安全浏览 |
| 27 | `99146c54-4f37-4ab8-9327-5f3291665e1e` | 浏览器自身 UI | 关闭时自动清除浏览数据 |
| 28 | `9f3f70fc-5afc-4958-a7b7-3bb4fcb01805` | 纯网页 | nba.com 商城筛选 |
| 29 | `9f935cce-0a9f-435f-8007-817732bfc0a5` | 纯网页 | justice.gov 表单列表 |
| 30 | `a728a36e-8bf1-4bb6-9a03-ef039a5233f0` | 纯网页 | dmv.virginia.gov 驾照条件 |
| 31 | `a96b564e-dbe9-42c3-9ccf-b4498073438a` | 纯网页 | FlightAware 论坛找回复最多的帖子 |
| 32 | `ae78f875-5b98-4907-bbb5-9c737fc68c03` | infeasible | 每页显示 50 条搜索结果 |
| 33 | `af630914-714e-4a24-a7bb-f9af687d3b91` | 浏览器自身 UI | chrome://settings/appearance 字号调到最大 |
| 34 | `b070486d-e161-459b-aa2b-ef442d973b92` | 纯网页 | drugs.com 副作用 |
| 35 | `b4f95342-463e-4179-8c3f-193cd7241fb2` | 纯网页 | recreation.gov 可预订日期 |
| 36 | `b7895e80-f4d1-4648-bee0-4eb45a6f1fa8` | 纯网页 | tripadvisor 酒店排序 |
| 37 | `bb5e4c0d-f964-439c-97b6-bdb9747de3f4` | 浏览器自身 UI | 默认搜索引擎改 Bing |
| 38 | `c1fa57f3-c3db-4596-8f09-020701085416` | 纯网页 | united.com 行李费计算器 |
| 39 | `cabb3bae-cccb-41bd-9f5d-0f3a9fecd825` | 纯网页 | kohls.com 玩具排序 |
| 40 | `da46d875-6b82-4681-9284-653b0c7ae241` | 纯网页 | mbta.com 预约表单填写 |
| 41 | `e1e75309-3ddb-4d09-92ec-de869c928143` | 需桌面侧 | 网页打印为 PDF 存到 Desktop(打印预览原生对话框 + 文件保存) |
| 42 | `f0b971a1-6831-4b9b-a50e-22a6e47f45ba` | 纯网页 | nfl.com 超级碗比分 |
| 43 | `f3b19d1e-2d48-44e9-b4e1-defcae1a0197` | 纯网页 | ticketek FAQ |
| 44 | `f5d96daf-83a8-4c86-9686-bada31fc66ab` | 纯网页 | apple.com 机型对比 |
| 45 | `f79439ad-3ee8-4f99-a518-0eb60e5652b0` | 纯网页 | ryanair 查航班 |
| 46 | `fc6d8143-9452-4171-9459-7f515143419a` | 纯网页 | delta.com 查航班 |

## 复现方式

A 节的三张表可用如下方式机器复现（归一化规则见开头）：

```python
import json, glob, itertools
from collections import Counter
NORM = {'Chrome': 'chrome', 'browser': 'chrome', 'OS': 'os', 'vscode': 'vs_code',
        'writer': 'libreoffice_writer', 'Writer': 'libreoffice_writer', 'calc': 'libreoffice_calc'}
combo, single, pair = Counter(), Counter(), Counter()
for f in glob.glob('OSWorld/evaluation_examples/examples/multi_apps/*.json'):
    apps = sorted({NORM.get(a, a) for a in json.load(open(f)).get('related_apps', [])})
    combo[' + '.join(apps)] += 1
    for a in apps:
        single[a] += 1
    for p in itertools.combinations(apps, 2):
        pair[tuple(sorted(p))] += 1
```

B / C / D 节的分类是人工标注，无法机器复现，但每条结论都对应附录里的具体任务 id，可逐条核对原始 JSON。
