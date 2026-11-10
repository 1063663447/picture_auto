# 9.6.6 · 框选改图（点基准图 → 画框写要求 → 交给 AI 改）

一句话：**基准图有歧义时，运营直接在图上把「哪块要改」框出来、每个框写一句要改成什么，
标注图连同框的位置一起发给 AI 和生图接口**，改图命中率比纯文字描述高，返工更少。

## 客户要的是什么

> 让用户能够直接点击要改的基准图 → 展示大图 → 给用户提供框框 → 框出有歧义的部分并在对应框框下
> 注明要改的效果或问题 → 提供给 harness 进行分析和提示词的修改（也可以同步将带框的基准图
> 返给生图接口方便参考），提高修改成功率。

## 用户怎么用（四步）

1. 基准图出来后，**点那张图**（或点卡片上的「框选改」）→ 弹出大图「框选要改的地方」。
2. 在图上**按住鼠标拖**出一个框（可画多个；每个框自动编号 1、2、3…）。
3. 在图下面每个框对应的输入框里写要求，例如「领口再宽 1.5cm，别勒脖子」「下摆走线歪了，要平直」。
4. 点「**交给 AI 改**」→ 标注图存盘 → AI 对话框自动打开、要求已经填好 →
   发送后 AI 按框位改提示词并调改图任务，出图后在对话里返给你看。

框外的一切默认保持不动：对话框和提示词都会写明「框外不要动，标注框线不要画进成品」。

## 后端做了什么

| 位置 | 变化 |
| --- | --- |
| `api.py` | 新增 `POST /api/jobs/{id}/annotations`（multipart：合成好的标注 PNG + `boxes` JSON）、`GET /api/jobs/{id}/annotations` |
| `service.py` | `save_annotation` / `annotations_for` / `create_rework_job(annotation_path=…)`：标注图拷进 `work/annotation*.png` 并写进 `params["annotation_path"]`；`reject_master(..., annotation_id=…)` 带着它建改图任务 |
| `runner.py` | 改图任务的请求里 **IMAGE 2 = 标注图**，并在提示词后追加 `annotation_clause(2)` |
| `prompts.py` | `ANNOTATION_CLAUSE`：框线/编号是「改动地图」不是设计，绝不能画进成品；只改框内，框外保持原样 |
| `api.py`（harness） | 会话上下文带上 `annotation_id` 与 `annotations`（每个框的编号、位置、要求文字），AI 知道「框1=领口 要加宽」 |

`boxes` 的坐标是 0~1 的相对值（`x/y/w/h`），与图片分辨率无关，存储和压缩都不变形。
越界路径、空 task_id、非法 JSON 都会被 400 拒掉。

## 前端做了什么

- `index.html` 新增 `#annotateDialog`（大图 + SVG 框层 + 每框一行要求 + 撤销/清空/取消/交给 AI 改）。
- `app.js` 新增标注模块：`openAnnotator` / `renderAnnoLayer` / `composeAnnotatedImage` /
  `submitAnnotation`；`harnessPayload` 带上 `annotation_id` 与 `annotations`。
- `annotate.css`：弹窗、框层、每框备注行的样式；图片本身 `pointer-events:none`，
  拖拽始终落在 stage 上（不会因为点在图上而丢事件）。
- 顺手修了两个轮询相关的体验问题（这两个都会让运营「点了没反应」）：
  1. `renderReview` 每 1.2s 轮询都会重建卡片 DOM，正在点「框选改」时节点被换掉 →
     现在按 `id|approved|缩略图` 算签名，**没变化就不重建**。
  2. 同一个改图任务被服务端上报两次时，对话里会出现**两张一模一样的「改后」卡片** →
     现在按改图任务 id 去重（`state.reworkJobId`）。
  3. 基准审核期已经提交了改图任务，状态条却还写「本轮尚未提交生图」→ 新增
     `reworking`（改图任务已提交），由 `acceptHarnessJob` 在 master 模式下切过去。

## 验证（都是跑了才写的）

```
python tools/check_annotation.py
  ① 标注落盘：图 + 框 + 备注
  ② 改图带上标注图（IMAGE 2）+ 提示词约束
  ③ harness 上下文：框的位置与要求都给了 AI
  全部通过
```

真实浏览器（mock 生图接口 + 真 LLM 对话）走了两轮完整流程：

| 步骤 | 结果 |
| --- | --- |
| 点基准图 / 点「框选改」 | 弹出「框选要改的地方」，标题写明 `23 索罗娜短袖 · 白色 框外保持不动` |
| 在图上拖框 | 出现 `rect.box` + 编号 1，下方出现「框 1」要求输入行 |
| 写要求 → 交给 AI 改 | 标注 PNG 合成成功（框线 + 红色编号徽标），`POST /annotations` 200，返回 `work/annotations/item-01-view-01__white-*.png` |
| 对话框 | 自动打开并已填入「按我框选的位置改「23 索罗娜短袖 · 白色」：框1：领口再宽 1.5cm…」 |
| 发送后 | AI 调 `reject_master` 建改图任务 `20261110-175910-19a4`，`params.annotation_path = work/annotation.png`，提示词含 `OPERATOR ANNOTATION MAP` |
| 返图 | 对话里出现 1 张「白色·改图 · 改后」卡片 + 「用这张当基准，开始改色 / 还要改…」 |
| 第二轮（下摆） | 状态条显示「改图任务已提交」，卡片仍然只有 1 张（去重生效） |

回归：`check_annotation / check_master_gate / check_master_chat / check_master_partial /
check_silent_distill / check_auto_learn / check_catalog / check_style_prompts / check_web_static /
check_blackglass_static / check_ui_slim` 全绿。
（`check_poll_traffic.py`、`check_rework_flow.py` 需要自带临时服务器 8790/8800 与指定 job id，
不属于常规套件，本次未跑。）

## 已知边界

- 框只按 **矩形** 记录；旋转/异形体请拆成两个框。
- 标注图会作为 **IMAGE 2** 一起发，所以改图请求的图片数量从 2 张变 3 张（基准/原图/标注），
  成本按模型计价规则走，没有额外调价。
- 「框选改」只在基准图待确认时出现；确认后要再改就走成品卡片上的「批量改图」。
- 前端资源版本号：`?v=9.6.14`（改完 app.js / ai-workflow.js 记得加号，浏览器才会拿新的）。
