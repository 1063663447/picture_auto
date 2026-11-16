# 9.6.35 / 9.6.36 · 新功能：保留模特换装

## 客户要的
分别放入**模特图**和**图案图**，保持模特和姿势不变，仅更换衣服的图案和材质。

## 怎么用
左侧「原始素材」下面多了一个勾选框 **保留模特换装**：

1. 上面照常拖入**模特图**（人穿衣服的实拍图，一张或多张）；
2. 勾上「保留模特换装」→ 出现「**选择图案 / 面料图**」，选那张要换上去的印花/面料图；
3. 按钮变成「**换装生成 N 张**」，点它开始。
   勾上之后不再选款式和颜色（1 张模特图 = 1 张成品），成品区照常看进度、下载。

## 提示词怎么写的（`prompts.build_model_swap`）
- **MODEL AND POSE LOCK**：IMAGE 1 是模特图 —— 脸/身份/发型/肤色/身材/姿势/手位/机位/构图/背景/
  光线全部保持，不许修图、瘦身、美颜、换人、加人、改姿势、动背景；
- **GARMENT MATERIAL AND PRINT SWAP**：只改两件事 ——
  ① PRINT 取 IMAGE 2 的图案（同字母、同颜色、同比例、同位置；IMAGE 2 没印花就不许自己编）；
  ② FABRIC 取 IMAGE 2 的材质（针织结构/织纹/厚薄/垂坠/光泽/底色），要能看出纹理，不许抹平；
- **KEEP THE CUT**：衣服的版型、领型、袖长、袖宽、下摆保持模特身上那件，不许改款；
- 负面词：不许动模特、不许改版型、不许留旧印花、不许编新印花、不许两件衣服叠加。

## 工程实现
- `service.create_model_swap_job()`：模特图逐张落 `inputs/item-NN/`，图案图存 `inputs/pattern.*`，
  记 `params.kind="model_swap"` / `params.pattern_image`；`review_gate=False`（没有派生色）、
  `shape_lock=off`；一张模特图一条任务（`color="pattern"`，显示「换装」）。
- `runner._run_model_swap()` / `_execute_model_swap()`：每次调用发**两张图**
  （模特图 + 图案图），并强制关掉 `input_fidelity`（高保真会把原印花/原面料钉死，图案就换不上）；
  接口只收 1 张图时直接报错说明，不会硬跑。
- `POST /api/jobs` 新增 `pattern` 文件字段，`mode=model_swap` 时在「必须选款式」之前分流。
- `_runner_for` 对 `model_swap` 跳过「款式×颜色」解析（和 rework 一样）。
- 前端：`swapToggle` / `swapPatternInput` + `createJob` 里走 model_swap 分支；
  张数、按钮文案（`WorkbenchUI.syncConfig`）都按换装模式显示。

## 验证
- 真机两次（terra · gpt-image-2.6-sunburst，各 1 张 ≈ $0.038）：
  * 模特图（小男孩黑卫衣） + 黑印花卫衣 → 同一男孩同一姿势，身上换成那张黑印花卫衣；
  * 模特图 + 白印花卫衣（06 连帽白色） → 同一男孩同一姿势，换成白色印花卫衣 + 白色针织面料。
  脸、姿势、手位、背景、礼盒、文字贴纸全部没变。
- 新增 `tools/check_model_swap.py`（24 项离线）：提示词三块、service 建任务的每一处、runner 发两张图、
  API 分流顺序、前端入口与拦截。
- `selftest`、`check_regenerate`、`check_white_cancel_style`、`check_pause_tasks` 全绿；
  缓存号 `?v=9.6.36`。

## 说明
- 图案图是**整套衣服**时（例如连帽卫衣），裤子/同套会跟着一起换材质——因为参考图里就是一套。
  只想换上衣的话，图案图请用「单件上衣」的照片。
- 换装是「不选款色」的独立模式；要继续批量改色，把勾选框取消即可回到原来的流程。
