#!/usr/bin/env python3
"""从真实 agent 的轨迹里找出**什么在迷惑它们**——只统计，不下结论。

为什么要专门做这个：到目前为止的缺陷都是我自己撞出来的，而我和 agent 的
失败方式不一样。我卡住会停下来查根因，agent 卡住会**换一条路**——于是它
遇到的摩擦不会变成报错，只会变成多出来的步数、重复的调用、放弃语义通道退回
坐标。那些才是"不好用"的真实证据，而它们只存在于轨迹里。

统计这几件事，每一件都对应一个可以动手改的东西：

  重复调用      同一个工具连着打同样的参数 → 它没从上一次的回答里得到答案
  错误率        哪个工具最容易报错、报什么错 → 描述或行为有歧义
  语义→坐标     在同一个元素上先 click 后 click_xy → 语义通道让它失望了
  探索开销      get_app_state / find 占全部调用的比例 → 观测太贵或太不精确
  放弃点        最后几步在做什么 → 它是怎么放弃的
  工具搭配      哪两个工具总是连着出现 → 也许该合成一个

用法：scripts/analyze-agent-traces.py [轨迹目录或文件…]
默认扫 /tmp/osworld-agent-*.jsonl
"""

import collections
import glob
import gzip
import json
import os
import sys


def load(path):
    """从 stream-json 轨迹里抽出 (工具调用, 结果) 序列。"""
    steps = []
    pending = {}
    final = ""
    # 存档里的轨迹是 gzip 的：原始 26 条含 base64 截图有 172MB，剥掉图 16.7MB，
    # 压缩后 2.1MB。分析只需要结构，不需要像素。
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = (block.get("name") or "").replace("mcp__ocu__", "")
                        pending[block.get("id")] = {
                            "tool": name, "input": block.get("input") or {},
                            "result": "", "error": False,
                        }
                        steps.append(pending[block.get("id")])
            elif kind == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") != "tool_result":
                        continue
                    entry = pending.get(block.get("tool_use_id"))
                    if entry is None:
                        continue
                    content = block.get("content")
                    text = ""
                    if isinstance(content, list):
                        text = "".join(c.get("text", "") for c in content
                                       if isinstance(c, dict))
                    elif isinstance(content, str):
                        text = content
                    entry["result"] = text
                    entry["error"] = bool(block.get("is_error"))
            elif kind == "result":
                final = event.get("result") or ""
    return steps, final


def signature(step):
    """一次调用的"意图指纹"：工具 + 它真正指向的东西。"""
    data = step["input"]
    target = (data.get("element_index") or data.get("element")
              or data.get("key") or data.get("text") or data.get("action")
              or data.get("app") or "")
    return step["tool"], str(target)[:60]


def main():
    paths = sys.argv[1:] or (
        sorted(glob.glob("/tmp/osworld-agent-*.jsonl"))
        or sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs", "osworld", "traces", "osworld-agent-*.jsonl.gz"))))
    if not paths:
        print("没有找到轨迹文件")
        return 1

    tools = collections.Counter()
    errors = collections.Counter()
    error_texts = collections.Counter()
    repeats = collections.Counter()
    pairs = collections.Counter()
    fallback = collections.Counter()
    total_steps = 0
    runs = 0

    for path in paths:
        steps, _final = load(path)
        if not steps:
            continue
        runs += 1
        total_steps += len(steps)
        seen = collections.Counter()
        for index, step in enumerate(steps):
            tools[step["tool"]] += 1
            if step["error"]:
                errors[step["tool"]] += 1
                head = (step["result"] or "").strip().splitlines()
                if head:
                    error_texts[head[0][:90]] += 1
            key = signature(step)
            seen[key] += 1
            if seen[key] > 1:
                repeats[key[0]] += 1
            if index + 1 < len(steps):
                pairs[(step["tool"], steps[index + 1]["tool"])] += 1
            # 语义 → 坐标的退让：同一次运行里先 click 后 click_xy
            if step["tool"] == "click_xy":
                for earlier in steps[:index]:
                    if earlier["tool"] == "click":
                        fallback["click → click_xy"] += 1
                        break

    print("扫了 {} 条轨迹，共 {} 步\n".format(runs, total_steps))

    print("=== 工具调用占比（观测 vs 动作）===")
    observation = {"get_app_state", "find", "get_screenshot", "list_apps", "verify"}
    obs = sum(v for k, v in tools.items() if k in observation)
    print("  观测类 {} 次（{:.0f}%），动作类 {} 次".format(
        obs, 100.0 * obs / max(total_steps, 1), total_steps - obs))
    for name, count in tools.most_common():
        bar = "█" * max(1, round(30.0 * count / max(tools.most_common(1)[0][1], 1)))
        print("  {:<22} {:>4}  {}".format(name, count, bar))

    print("\n=== 报错最多的工具 ===")
    if errors:
        for name, count in errors.most_common(6):
            print("  {:<22} {:>3} 次错 / {} 次调用 = {:.0f}%".format(
                name, count, tools[name], 100.0 * count / max(tools[name], 1)))
    else:
        print("  （没有报错）")

    print("\n=== 最常见的错误原文 ===")
    for text, count in error_texts.most_common(8):
        print("  {:>2}×  {}".format(count, text))

    print("\n=== 重复调用（同工具同目标打了不止一次）===")
    if repeats:
        for name, count in repeats.most_common(6):
            print("  {:<22} {:>3} 次重复".format(name, count))
        print("  ↑ 重复意味着 agent 没从上一次的回答里拿到它要的信息")
    else:
        print("  （没有重复）")

    print("\n=== 语义通道退让 ===")
    for name, count in fallback.most_common():
        print("  {:<22} {:>3} 条轨迹里出现".format(name, count))
    if not fallback:
        print("  （没有出现）")

    print("\n=== 最常见的相邻工具对 ===")
    for (first, second), count in pairs.most_common(8):
        print("  {:<20} → {:<20} {:>3}".format(first, second, count))
    print("  ↑ 总是成对出现的，也许该合成一个工具")
    return 0


if __name__ == "__main__":
    sys.exit(main())
