# 9.6.32 · 多张基准图可以「边跑边确认」，不用等其它图生成完

## 客户现场（截图）
一批里有 2 张基准图（06 连帽卫衣 · 白色、10 弹力短袖 · 白色）。先出图那张想点「确定」，
另一张还在生成，弹出「**任务正在运行中，等这一轮结束再确认**」，只能干等。

## 为什么
`approve_master` 开头有一道硬拦：

```python
if job_id in self._tasks and not self._tasks[job_id].done():
    raise WorkbenchError("任务正在运行中，等这一轮结束再确认")
```

而且前端按钮的可用条件 `review.can_approve` 也要求 `job.status == "review"`，
所以「这一轮还在跑」时根本不给确认。

## 改成什么
确认**立刻生效**，刚确认那张基准的派生色**直接排进正在跑的那一轮**：

- `service.approve_master`：去掉硬拦；记完 approved / adopt / 日志后
  - 这一轮还在跑 → 调 `runner.kick()`，把这张基准的派生色插进本轮；
    没有可跑的就什么都不做（其它基准出完自然回到审核态）
  - 没在跑 → 照旧另起一轮派生
  - 顺带把 runner 实例记到 `self._runners[job_id]`（`_forget` 里清掉）
- `runner.kick() / _kick_approved()`：只挑「已确认基准 + 还没开始 + 没暂停 + 没出图」的派生色，
  用 `self._kicked` 去重，然后走原来的 `_gather(..., _execute_shape_task)`
- `runner._drain_extra()`：这一轮收尾前把插进来的这批等完（出完基准后、跑完整轮后各一次），
  别让「已排进本轮」的图在任务收尾后被丢下
- `_gather` 的并发登记从「整体赋值」改成「并集」，多批同时跑不会互相把 `_tasks_running` 清空
  （取消时要用它把在跑的协程停掉）
- `review.can_approve` 不再要求 `status == "review"`：只要有「已出图、未确认、未暂停」的基准图就能点

**并发没有放宽**：新排进来的派生色照样走 `self.sem`（本机并发）和飞书闸门（`_guarded_call` 里排队），
只是不再排在「等整轮结束」后面。

## 验证
- 离线 `tools/check_approve_while_running.py`（11 项）：
  有 runner 时 approve 不报错、标记已确认、调了 `kick()`、没有另起任务；没 runner 时照旧另起一轮；
  `_kick_approved` 只挑该跑的、不重复排；`_gather` 并集登记；service 不再硬拦。
- 真机 E2E（mock，3 个款式各 2 色，0.3s 轮询抢窗口）：
  第一张基准一出就确认（此时其余 2 张仍在生成）→ 返回 200、没有「等这一轮结束再确认」、
  被确认那张的派生色**都出图了**、其余款式正确回到「等确认」（不会被一起确认掉）、
  日志出现「先把这些颜色排进本轮」。
- `check_pause_tasks`、`check_cancel_button`、`check_rework_recovery`、`selftest` 全绿；缓存号 `?v=9.6.32`。

## 说明
- 「全部确定」还是只确认当时已经出图的那几张；还在生成的等它出来再点（或者等它自动出现在审核条上）。
- 被暂停的基准图不会被这张的确认带上（各自的暂停状态互不影响）。
