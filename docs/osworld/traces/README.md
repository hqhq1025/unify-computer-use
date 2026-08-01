# 真实 agent 轨迹存档

这里是每一次 `osworld-bench.py agent` 跑出来的**原始 stream-json 轨迹**，
以及 MCP 自己写的 step trace。

**为什么要进版本库**：这些文件原本只在 `/tmp`，重启就没了。而本轮多数产品
缺陷不是我撞出来的，是从这些轨迹里**统计**出来的——

  · 13 条轨迹里 12 条的开局是 `list_apps → get_app_state`，
    这才让我去量应用名的三种风格并把规则写进工具描述
  · 60 次观测调用里 8 次（13%）响应达 114–119KB，被客户端整块换成
    `<persisted-output>` 文件指针，而跑测里 Read 是禁用的——
    这条直接推出了"树预算的单位应该是字符不是节点"
  · verify 被调用 5 次失败 3 次，**三次全是 exists:true 超时**，
    于是给它的失败补上了"最接近的候选"

结论可以被推翻，但只有原始数据在，别人才推翻得了。

  osworld-agent-<taskid>.jsonl   Claude Code 的完整 stream-json 轨迹
  osworld-trace-<taskid>.jsonl   MCP 的 step trace（每动作一行，含前后状态摘要）

分析脚本：`scripts/analyze-agent-traces.py`
