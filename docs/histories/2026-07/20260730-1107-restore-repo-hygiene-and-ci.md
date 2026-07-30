## [2026-07-30 12:05] | Task: 补齐仓库卫生文件，让 make ci 能跑到底

### 🤖 Execution Context
* **Agent ID**: `claude-code`
* **Base Model**: `Claude Opus 5`
* **Runtime**: `Claude Code / Linux x86_64（Ubuntu 22.04）`

### 📥 User Query
> 按 execution plan 推进待办 #23：`make ci` 跑不到底。

### 🛠 Changes Overview
**Scope:** 仓库根目录配置与 `.github/`。不涉及 runtime 代码。

**Key Actions:**
- **补齐 11 个 `check-repo-hygiene.sh` 要求但从未存在的文件**：`.editorconfig`、
  `.markdownlint.json`、PR 模板、3 个 issue 模板、`dependency-review-config.yml`，
  以及 4 个 workflow（`ci` / `docs-check` / `repo-hygiene` / `supply-chain-security`）。
- **workflow 全部固定 action SHA**，复用 `release.yml` 里已有的同款 SHA，
  通过 `check-action-pinning.sh`。
- **CI workflow 直接复用仓库自己的检查脚本**，不另起一套：Go 侧跑 `gofmt`/`vet`/`test`，
  Python 侧装 `python3-gi` + `gir1.2-atspi-2.0` 后跑 `runtime_test`（假节点驱动，
  不需要桌面会话），脚本侧跑 `bash -n` / `node --check`。

### 🧠 Design Intent (Why)
*`check-repo-hygiene.sh` 要求的 11 个文件在本仓库和 upstream 里**都不存在**——
它是 agent 模板遗留的期望，项目从未兑现。于是 `./scripts/ci.sh` 在第二步就退出，
后面的 Go 测试、Python 测试、脚本语法检查**全都跑不到**。*

*这不是"少个 lint"的问题：本计划后面还有二十多项改动，每一项的验收都写着
"以下全部通过"，而实际上 CI 根本没跑到那些检查。守不住的 CI 比没有 CI 更危险，
因为它会给出通过的假象。*

*workflow 里刻意不重新实现检查逻辑，而是调用 `scripts/` 下已有脚本——
保证本地 `make ci` 和 GitHub 上跑的是同一套判据，不会出现"本地过 CI 不过"。*

*issue 模板里专门加了一条"请说明工具返回的 isError 与实际观察到的状态是否一致"，
因为本项目已知的主要缺陷类型就是"报成功但没生效"，这两者的差异是最关键的信息。*

### ⚠️ 对外可见的副作用
新增的 4 个 workflow 会在 push / PR 时**真实运行**。如果不希望现在就在 GitHub 上
跑 CI，删掉 `.github/workflows/` 下这四个文件即可，其余卫生文件不受影响
（但 `check-repo-hygiene.sh` 会重新变红）。

### ✅ Verification
- `./scripts/check-repo-hygiene.sh` passes（此前失败，缺 11 个文件）。
- `./scripts/check-action-pinning.sh` passes。
- **`./scripts/ci.sh` 完整跑通，退出码 0**——此前在第二步即退出。
- 全量：Go 65 个用例 + Python 65 个用例通过。

### 📁 Files Modified
- `.editorconfig`、`.markdownlint.json`（新增）
- `.github/PULL_REQUEST_TEMPLATE.md`、`.github/dependency-review-config.yml`（新增）
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`（新增）
- `.github/workflows/{ci,docs-check,repo-hygiene,supply-chain-security}.yml`（新增）
