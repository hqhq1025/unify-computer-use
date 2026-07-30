## 这次改动做了什么

<!-- 一两句说清目的，不要复述 diff -->

## 为什么这么做

<!-- 设计动机。如果修的是 bug，说明根因，而不只是现象 -->

## 验证方式

<!-- 贴实际跑过的命令与结果。缺陷修复请说明回归测试在改动前是失败的 -->

- [ ] `make check-repo`
- [ ] `(cd apps/OpenComputerUseLinux && go test ./... && python3 -m unittest runtime_test)`
- [ ] 真实桌面验证（如涉及 Linux runtime）

## 文档同步

- [ ] `docs/histories/` 已记录本次代码变更
- [ ] 行为变化已同步到 `docs/ARCHITECTURE.md` 或相关文档
