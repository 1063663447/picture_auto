# 9.6.11 · 统计数字置顶（成品区再多约 60px 高度）

客户要求：把

```
总任务 14 · 已完成 7 · 失败 0 · 本次花费 ¥0.266
```

这一栏**置顶**，与「服务已连接 · 0 张原图 · 3 款 · 0 张预计成品」对齐，腾出空间给成品图片。

## 改动

| 位置 | 以前 | 现在 |
| --- | --- | --- |
| 统计条 | `#galleryPane` 顶部，4 格大块（`min-height:59px` + 上下 padding + 卡片底 + 12px 外边距 ≈ 71px） | `.flow-bar` 右侧的 `.kpis-top`：一行小字数字，跟「服务已连接 / N 张原图 · M 款」同一水平带 |
| 视觉 | 带边框的 4 格仪表 | 无底无框，`总任务 14 │ 已完成 7 │ 失败 0 │ 花费 ¥0.266/0.532`，竖线分隔，右对齐 |
| 成品区 | 被统计块占 71px | 整块还回去 |
| 审核条头部 | `flex-wrap:wrap`，窄了会变两行 | 固定一行（徽标 + 计数 + 全部确定），中间文字省略 |

窄屏：≤900px 只留数字（标签隐藏）；≤760px 整条隐藏（手机上方另外有「画布 N 张」的已完成数）。

## 实测（1088×638 窄窗口）

| 量 | 结果 |
| --- | --- |
| `.flow-bar` | top 57 · 高 43 |
| `.kpis-top` | (330, 71) 741×15，背景 `rgba(0,0,0,0)`、边框 0、padding 0 —— 就是一行字 |
| 单个 KPI | 61×15，与左上角「服务已连接」药丸（top 66、高 24）居中对齐（都在 y≈78） |
| 旧统计块 | `#galleryPane .kpis` 已不存在（`false`） |
| 成品区起点 | `#galleryPane` top 385（紧跟审核条尾部 375） |

> 附注：顶栏数字用 `animateNumber()` 递增，是 `requestAnimationFrame` 驱动；在后台标签页里
> rAF 被节流会停在旧值（后台测量时看到 0 就是这个原因，前台正常）。

## 回归

`check_web_static / check_blackglass_static / check_ui_slim / check_master_chat / check_master_gate /
check_master_partial / check_annotation / check_revert / check_concurrent_rework / check_silent_distill /
check_auto_learn / check_catalog / check_style_prompts` 全绿。

前端资源版本 `?v=9.6.22`。
