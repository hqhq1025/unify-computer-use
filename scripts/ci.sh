#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${repo_root}/scripts/check-docs.sh"
"${repo_root}/scripts/check-repo-hygiene.sh"
"${repo_root}/scripts/check-action-pinning.sh"

while IFS= read -r file; do
  bash -n "$file"
done < <(find "${repo_root}/scripts" -type f -name '*.sh' | sort)

while IFS= read -r file; do
  node --check "$file"
done < <(find "${repo_root}/scripts" -type f -name '*.mjs' | sort)

if command -v go >/dev/null 2>&1; then
  (
    cd "${repo_root}/apps/OpenComputerUseWindows"
    go test ./...
  )
  (
    cd "${repo_root}/apps/OpenComputerUseLinux"
    go test ./...
  )
fi

if command -v python3 >/dev/null 2>&1; then
  python3 -m py_compile "${repo_root}/apps/OpenComputerUseLinux/runtime.py"

  # apps/OpenComputerUseLinux/tests/ 下是需要真实桌面和目标应用的集成测试，
  # CI 里跑不了，但至少保证它们语法可用、不会悄悄烂掉。
  while IFS= read -r file; do
    python3 -m py_compile "$file"
  done < <(find "${repo_root}/apps/OpenComputerUseLinux/tests" -type f -name '*.py' 2>/dev/null | sort)
  python3 -m py_compile "${repo_root}/scripts/verify-linux-input-chain.py"

  # runtime_test.py 用假的 AT-SPI 节点驱动，不需要桌面会话，但仍要 import
  # runtime.py，因此依赖 PyGObject 的 Atspi typelib。没装就跳过，不要让
  # 缺少桌面依赖的机器直接挂掉 CI。
  if python3 -c 'import gi; gi.require_version("Atspi", "2.0")' >/dev/null 2>&1; then
    (
      cd "${repo_root}/apps/OpenComputerUseLinux"
      python3 -m unittest runtime_test
    )
  else
    echo "跳过 Linux runtime 单测：缺少 PyGObject 的 Atspi typelib"
  fi
fi

echo "基础 CI 检查通过"
