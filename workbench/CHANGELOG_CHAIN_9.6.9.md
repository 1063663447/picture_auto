# 9.6.9 · 第 2 轮之后还能接着框选改（第 3 轮、第 4 轮…，不用先采纳）

以前：改完一轮，卡片上只有「这张可以 / 还要改 / 直接说」——「还要改」回到的是**第 1 轮那张图**上重新框，
没法在刚改出来的那张图上继续提要求。要连改就得先「这张可以」采纳入库，再走成品批量改图。

现在：**被改出来的那张图本身就是下一次框选的底图**，一路往前推，主任务的基准图一直停在待确认，想改几轮改几轮，
最后一轮满意了再「这张可以」。

## 用起来是什么样

1. 基准图 → 点图框选 → 交给 AI → 卡片转圈 → 出图变「第 2 轮 · 待确认」。
2. 不点「这张可以」，直接点「**接着改（第 3 轮）**」→ 打开的就是**第 2 轮那张图**（标题写明「（第 2 轮）」）→
   继续画框写要求 → 交给 AI → 卡片变「第 3 轮 · 待确认」。
3. 反复即可；「这张可以」永远采纳**最新一轮**（`approve_master` 的 `adopt` 指向最新任务/图片）。
4. 点图（不是按钮）也一样：第 2 轮/第 3 轮的图点开就是那一张，继续框。
5. 「直接说」也行：对话里改的就是最新那张（`source_job_id`/`source_task_id` 随请求带上）。

## 后端怎么串的

| 位置 | 改动 |
| --- | --- |
| `api.py`（master 分支） | 新增 `source_job_id` + `source_task_id`：本轮的主角是**上一轮那张图**（可能在改图任务里），目标集合从它解析；主任务 `job_id` 仍用于审核/采纳/统计 |
| `api.py`（`tool_reject`） | 把 `ctx.source_job_id` / `ctx.source_task_ids` 透传给 `reject_master` |
| `agent.AgentContext` | 新增 `source_job_id` / `source_task_ids` 两个字段 |
| `service.reject_master` | 新增同名参数：给了就从那个任务的那张图重画；标注图也按**被框的那张图所在任务**解析（`_annotation_path(src_job, …)`） |
| `service.create_rework_job` | 轮次进版本号：`-v2`（第 1 次改）→ `-v3` → `-v4`；颜色名去重（`白色·改图` 不会变成 `白色·改图·改图`）；新任务记 `rework_round` / `revises_job` / `revises_tasks` |
| 主任务日志 | 第 N 轮写成「基准图第 N 轮修改中…」；`params.master_reviews` 每条多记 `from_job` / `from_tasks` / `round` |

## 验证（真跑）

`python tools/check_concurrent_rework.py`（在 9.6.7 的两轮并发之上加了第 ⑤ 段）：

```
⑤ 不采纳，直接以第 2 轮那张图继续改（第 3 轮）
  [ok] 第 2 轮的图（item-01__26-cropped-sorona-tee__white-v2）
  [ok] 第 2 轮的 rework_round = 1
  [ok] 在第 2 轮的图上框选（200）
  [ok] 第 3 轮拿到任务 ['20261110-184301-e6ae', ...]
  [ok] 版本号接着涨（item-01__26-cropped-sorona-tee__white-v3）
  [ok] 第 3 轮 rework_round = 2
  [ok] 记录它改的是上一轮任务（20261110-184241-0297）
  [ok] 记录它改的是上一轮那张图（['...white-v2']）
  [ok] 第 3 轮的参考图就是第 2 轮那张      ← 字节比对，没偷偷退回第 1 轮
  [ok] 第 3 轮的提示词也带标注约束
  [ok] 主任务还在 review（没被强制采纳）
  [ok] 基准图仍未确认（可以不采纳就继续改）
```

回归：`check_annotation / check_master_gate / check_master_chat / check_master_partial /
check_silent_distill / check_auto_learn / check_catalog / check_style_prompts / check_web_static /
check_blackglass_static / check_ui_slim` 全绿。

## 已知边界

- 卡片上一次只显示**最新一轮**的图（历史各轮在任务记录里，可点任务历史看）。
- 轮次没有上限，但每一轮都是一次真实生图（照旧计费）。
- 「这张可以」采纳的是最新一轮；想回退到更早的轮次，得去对应改图任务里看，当前界面没做回退按钮。
- 前端资源版本 `?v=9.6.19`。
