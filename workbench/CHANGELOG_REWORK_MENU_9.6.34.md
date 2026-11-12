# 9.6.34 · 基准图的「取消这款」换成「重新生成」，三项收进「改图 ⌄」

## 客户要求
1. 基准图的「取消这款」改成「重新生成」；
2. 把基准图的「框选改」「直接说」「重新生成」集成成一个「改图」按钮，点开再显示这三项；
3. 改完打包。

## 界面
审核条上的基准图卡片现在只有两个按钮：**确定** + **改图 ⌄**。点「改图」弹三项：

| 菜单项 | 说明 |
|---|---|
| 框选改 | 在图上框出要改的地方，框外不动（走原来的标注 + AI 改图流程） |
| 直接说 | 跟 AI 说哪里不对，改完再确认（原来的「直接说」） |
| 重新生成 | 这张基准图重画一次（同提示词），旧成品清掉 |

弹出菜单用的是成品卡那套 `#reworkMenu`（挂在 body + fixed，不会被卡片裁剪），
靠 `data-kind="master"` 区分两套菜单的动作。

## 后端
- 新增 `service.regenerate(job_id, task_ids)` + `POST /api/jobs/{id}/regenerate`：
  * 清空这张的 `out / bytes / status(done→pending) / approved / adopted_from / review_note`；
  * 这一轮还在跑 → `runner.reschedule()` 排进当前这一轮（并发仍受 `sem` + 飞书闸门）；
    没在跑 → 另起一轮；
  * 完成后照旧回到「基准图待确认」，派生色不受影响。
- runner 新增 `reschedule() / _run_rescheduled()`：可以补跑**基准图本身**（`kick()` 只补派生色）。
- `cancel_style`（取消这款）保留在后端和 API，只是界面上不再放按钮。

## 验证
- 离线 `tools/check_regenerate.py`（19 项）：清空/复位/另起一轮、边跑时走 reschedule、
  非法输入、前端接线（菜单三项 + regenerateTask + 取消这款按钮已撤）。
- 真机 E2E（mock）：审核态点重新生成 → 200「重新生成中」→ 基准图重画后回到 review，
  成品文件时间戳更新、派生色没被动。
- 浏览器实测：审核卡片只有「确定 / 改图 ⌄」，点开菜单三项齐全（截图）。
- `check_white_cancel_style`、`check_pause_tasks`、`check_approve_while_running`、
  `check_card_rework_menu`、`selftest` 全绿；缓存号 `?v=9.6.34`。

## 便携包
`releases/PictureWorkbench-9.6.34-win-x64-internal.zip`
- 130 MB（136,175,014 字节），sha256 `9a1165e4ecc61f18d321aeace2e33f0742b30cf20aa07ee21b26774e229bef22`
- 386 个应用文件 / 20 个依赖 / 25 款式 115 色 115 效果图 / 29 份款式提示词
- **解压即用验证通过**：包内 runtime 起服务、`/api/health` 28 款 9 接口、首页 200、
  飞书价目 7 条、mock 出图并下载成功
- 群发文案 `发给同事_9.6.34.txt`，校验 `*.sha256` / `-verification.json`
