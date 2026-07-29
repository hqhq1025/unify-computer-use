"""超大容器守卫的回归验证。

背景：LibreOffice Calc 的 sheet 节点谎报 2^31 个子节点——它的 accessible range
是整张表（16384 列 × 1048576 行），getAccessibleChildCount() 返回 rows*cols，
经 D-Bus int32 截断后就是 2147483647
（sc/source/ui/Accessibility/AccessibleTableBase.cxx:274，上游注释自称
 "plain and simple madness"）。朴素遍历掉进该节点不是慢，是永远不会结束。

该缺陷在小配额下不暴露：遍历在走到 sheet 节点之前就被 max_tree_nodes 截断了。
必须把配额调大到足以穿过菜单树，才能复现。实测（Ubuntu 22.04 / LO 7.3 / 一个
30 行的 xlsx）：

    配额 1200 ->  无守卫 895ms ✓   有守卫 1043ms ✓   （都没走到 sheet）
    配额 5000 ->  无守卫 75s 超时 ✗ 有守卫 1386ms ✓   （走到了 sheet）

所以本测试固定用大配额，否则测不出任何东西。
"""
import json
import subprocess
import sys
import time

BIG_QUOTA = 5000
BUDGET_SECONDS = 30


def get_app_state(app, quota):
    t0 = time.time()
    p = subprocess.run(
        ["open-computer-use", "call", "get_app_state",
         "--args", json.dumps({"app": app, "max_tree_nodes": quota})],
        capture_output=True, text=True, timeout=BUDGET_SECONDS * 3,
    )
    elapsed = time.time() - t0
    try:
        return json.loads(p.stdout), elapsed
    except json.JSONDecodeError:
        return None, elapsed


def main():
    app = sys.argv[1] if len(sys.argv) > 1 else "soffice"
    result, elapsed = get_app_state(app, BIG_QUOTA)
    print(f"配额 {BIG_QUOTA}，耗时 {elapsed:.2f}s")

    if result is None:
        print("FAIL: 返回非 JSON")
        return 1
    if result.get("isError"):
        text = (result.get("content") or [{}])[0].get("text", "")
        print(f"FAIL: {text[:120]}")
        print("      —— 若为超时，说明遍历掉进了自管理容器（守卫失效）")
        return 1

    text = next(b["text"] for b in result["content"] if b["type"] == "text")
    nodes = sum(1 for line in text.splitlines() if line.startswith("\t"))
    print(f"节点数 {nodes}")

    if elapsed > BUDGET_SECONDS:
        print(f"FAIL: 耗时超过 {BUDGET_SECONDS}s 预算")
        return 1
    if nodes < 100:
        print("FAIL: 节点过少，守卫可能过度剪枝")
        return 1

    print(f"PASS: 大配额下未掉进超大容器陷阱（{elapsed:.2f}s < {BUDGET_SECONDS}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
