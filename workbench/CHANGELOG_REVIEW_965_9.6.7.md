# 9.6.7 · 基准图卡片重排 + 框选对齐鼠标 + 交给 AI 就直接改、还能多张同时改

这一版修的是客户用 9.6.6 试出来的三个问题，外加一个真 bug（多款同批时框选只该改那一张）。

## 1「已跑完的图被挤到看不见」→ 卡片改成自适应网格

以前 `#reviewThumbs` 是 flex + 固定 132px 图，卡片越多越挤，已经确认的卡因为少了按钮行还会被压矮。
现在：

- `display:grid` + `repeat(auto-fill,minmax(200px,1fr))`：宽了自动多排，窄了自动换行，不再互相挤。
- 图片区固定 `aspect-ratio:1/1`、`object-fit:contain`：三张、五张并排都看得清。
- 按钮行留 `min-height:30px`：已确认 / 改图中 / 待确认三种卡的**高度一致**，不会再被组件挤扁。
- 窄屏（<760px）自动降到 `minmax(150px,1fr)`。

## 2「框的起点没对准鼠标」→ 框层贴到图片上

原因：图片在 `.annotate-stage` 里居中，两侧有留白，而框层 `<svg>` 铺满整个 stage。
`point()` 算的是「相对图片」的坐标，画的时候却按「相对 stage」渲染 → 一旦有留白，框就整体偏移。

修法：`index.html` 里把 `<img>` 和 `<svg>` 一起塞进 `.annotate-frame`（正好等于图片盒子），
框层改成 `position:absolute; inset:0` 相对 frame 定位。现在鼠标按在哪儿，框就从哪儿起。

顺带把 `stage.setPointerCapture()` 包了 try/catch：某些浏览器/笔式指针会抛 `NotFoundError`，
抛出去整段拖拽就废了（合成事件的自动化测试也被它卡过）。

## 3「交给 AI 改」→ 直接发出去、回到审核条、卡片自己转圈等第 2 轮

以前的流程：点「交给 AI 改」→ 打开对话框 → 用户还必须再点一次「发送补充要求」。
现在（客户要的）：

1. 点「交给 AI 改」→ 标注图存盘 → **直接**把「框位 + 每框要求 + 标注图」发给 AI，不用再点发送。
2. 立刻关掉框选、**回到审核条**（不再弹对话框），这张卡片进入「改图中」：
   图上盖一层半透明的「AI 正在读框选… / 第 2 轮基准图生成中…」+ 转圈动画。
3. 出图后卡片自动变成「**第 2 轮 · 待确认**」并显示新图，按钮换成
   「这张可以（= 采纳这张当基准，开始改色）/ 还要改 / 直接说」。
4. 等待期间**可以继续框选、改别的基准图**——每张一条任务，互不排队。

为此前端加了一个「安静轮询」通道 `streamQuiet()`：改图那一轮不抢对话面板，
只等服务端把任务建出来，然后按 `jobId` 单独盯进度（`watchReworkFor`，定时器按 jobId 分开存）。
关掉对话框也不会停这张卡的进度（`closeHarness` 不再 `stopReworkWatch`）。

## 4 顺手修的真 bug：多款同批时「只改这一张」会连累其它款

`api.py` 的 master 分支原来把**所有已出的基准图**都塞给 AI：

```python
task_ids = [t.id for t in (ready or masters)]   # ← 以前：无视前端点名
```

于是三张基准图待确认时，框选改其中一张，AI 可能把三张一起返工。
现在按前端点名的 `task_ids` 过滤（并校验收窄后非空）：

```python
wanted = {str(item) for item in task_ids}
targets = [t for t in (ready or masters) if not wanted or t.id in wanted]
if not targets: raise ValueError("指定的那张基准图还没出图，或不属于这条任务")
```

## 验证

**接口级并发自检（新增脚本，真跑真 LLM）**

```
python tools/check_concurrent_rework.py
  ① 造评审任务（3 张基准图）
  ② 给两张基准图各存一张标注图
  ③ 同时发两轮改图（并发）   第 1 轮 7.2s · 第 2 轮 6.5s，两个不同任务 id
  [ok] 改图任务只含 1 张 · 任务来自点名的这张 · 带上了标注图
  [ok] 提示词里有标注约束 · 提示词指明标注图是 IMAGE 2
  ④ 两张基准图各自的改图来源不同，没串任务
  全部通过
```

**真浏览器（假任务 + mock 生图 + 真 LLM）**

| 步骤 | 结果 |
| --- | --- |
| 3 张基准图的审核条 | 卡片自适应排布、图片约 260px 见方、三种状态一样高（已截图核对） |
| 拖框 | 鼠标 (370,164.5)→(470,254.5)，框正好画在 (370,164)~(470,254)，**起点跟手** |
| 写要求 + 交给 AI 改 | 不回对话框，直接回审核条；卡片变成「第 2 轮 · 待确认」并显示新图，其它两张仍是「第 1 轮」 |
| 服务端 | 只建了 1 条改图任务（1 张），`params.annotation_path=work/annotation.png` |

**回归**：`check_annotation / check_master_gate / check_master_chat / check_master_partial /
check_silent_distill / check_auto_learn / check_catalog / check_style_prompts / check_web_static /
check_blackglass_static / check_ui_slim` 全绿。

## 已知边界

- 「改图中」的进度是每 2.2s 拉一次任务状态，卡片上的小字不会逐秒跳；圈一直在转。
- 第 2 轮**不联网回改**（不能在「第 2 轮」的图上再框一次）：还想改就点「还要改」，回到第 1 轮那张上重新框；
  想在第 2 轮基础上继续，就先「这张可以」采纳入库，再对它走成品批量改图。
- 多次并发改图会各自计费（每个任务一次生图），界面上「本次花费」照旧累计。
- 前端资源版本 `?v=9.6.17`；改完 `app.js / ai-workflow.js / *.css` 记得加号。
