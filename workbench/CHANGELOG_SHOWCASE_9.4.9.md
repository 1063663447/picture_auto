# 产品展示图（9.4.9 附）· 6 张

## 一句话
素材全部取自工作台真机界面和 `data/jobs` 里已经跑出来的真实成品，走 **65535 网关的
`gpt-image-2.6-sunburst`** 直出，不需要跑款式库、不改任何业务数据。
**6 张（1 张总览 + 5 张分图）全部由生图接口生成，没有脚本拼图。**

## 产物（`workbench/showcase/`，已 gitignore）

| 文件 | 尺寸 | 展示什么 | 单张耗时 |
|---|---|---|---|
| `00-overview.png` | 1536×1024 | 总览主视觉：主界面 + 配色成品卡，纯黑舞台、青绿光带 | 84.6s |
| `01-hero.png` | 1536×1024 | 整体界面：创作空间、10 款 / 49 色、批量队列 | 93.2s |
| `02-styles.png` | 1536×1024 | 款式面板 + 12 张真实批量成品（同一原图 × 多款式） | 95.5s |
| `03-prompt.png` | 1024×1536 | 款式专属提示词正文特写（实际发给模型的全文） | 120.0s |
| `04-batch.png` | 1536×1024 | 批量成品网格 + 历史任务批次对账 | 121.4s |
| `05-recolor.png` | 1536×1024 | 原图 → 4 个颜色成品（同一人物 / 版型 / 印花） | 95.1s |
| `showcase-manifest.json` | — | 每张的接口、模型、尺寸、耗时、体积 | — |

成本：6 张 × $0.038 = **$0.228**（报价口径，见 `config.json` → `terra.model_prices`）。

## 总览怎么出的
总览不做拼图，也不追求把每张分图都摆上去——那样会变成说明书。
只喂两张**已经出好的分图**（`01-hero` + `05-recolor`）当参考图，让模型压成一个主视觉：

- 主界面当主角，略偏左、转 10°，后缘退远；
- 配色成品卡缩小放在右下，反向旋转，上缘轻压主角面板、明确在后；
- 一条青绿光带从两块面板背后斜穿，剩下全黑 + 地面反光；
- 50 mm f/4 全清晰，无眩光压字。

两块面板的内容仍然是 1:1 保真，画面里没有任何模型自己写的字。

## 关键做法：先把版面做死，再让模型去打光

中文是这个模型的短板。第一版 `03-prompt` 把卡片顶部的中文标题重绘成了「闪词」乱码，
而旁边的英文正文一字不差——所以改成三步：

1. **版面由真机像素决定**：`tools/make_showcase_materials.py` 用 PIL 把截屏裁成
   「整屏 / 款式面板 / 提示词正文 / 历史批次 / 成品网格」五类版面，文字位置不经过模型；
2. **提示词里写死 HARD CONTRACT**：逐字保留、不许重排 / 翻译 / 摘要 / 新增文字，
   模型只负责打光、材质、景深、氛围（"You are the photographer, not the designer."）；
3. **特写只给英文**：`03` 这版直接切掉中英混排里的中文标题行，模型没有中文可重绘。

结果：`01/02/04/05` 里的中文（款式名、色名、「10 款 / 49 色」、「历史任务」批次行）
全部与真机一致；`03` 纯英文正文零乱码。

另外 `board-03` 的裁剪坐标是靠探针量出来的（`scratch/showcase/probe_left.py`、
`probe_rows.py`）：先扫列找文字左边界、再扫行找中文标题行，避免裁掉正文首字母。

## 复现

```powershell
# 1) 截屏：用浏览器会话在 1900x1100 下截 4 张（raw-ui-01 / raw-ui-glow / raw-prompt-23 / raw-history）
#    放到 scratch/showcase/
# 2) 截屏 → 素材/版面
python tools/make_showcase_materials.py
# 3) 调接口出图（先 --dry-run 看参考图和字数，再 --only 挑着跑）
python tools/make_showcase.py --spec E:\picture_auto\scratch\showcase\spec.json --dry-run
python tools/make_showcase.py --spec E:\picture_auto\scratch\showcase\spec.json --only "01,03"
# 4) 总览要等 01 / 05 出好后再跑（它拿这两张当参考图）
python tools/make_showcase.py --spec E:\picture_auto\scratch\showcase\spec.json --only 00
```

提示词全文在 `scratch/showcase/spec.json`，6 段都可单独改；`--only` 支持按 id 前缀挑张数，
manifest 按 id 合并，分批跑不会冲掉上一批记录。
`tools/make_showcase_sheet.py`（拼图版总览）保留备用：需要带中文说明条的对照单页时再用，
正式交付的总览是 `00-overview.png`。

## 注意事项

- `showcase/` 和 `scratch/` 都在 gitignore 里：图片 13MB，素材和版面随时可重出。
- 成品图来自真实批次：06 连帽卫衣 3 色、25 长袖童装 5 色、21 童装索罗娜 2 色、
  23 索罗娜短袖 10 色，原图是 `20261110-104234-1463/inputs/item-01/view-01.png`。
- 「不许改字」这条对 `gpt-image-2.6-sunburst` 有效；换 grok / nano-banana 需要重测，
  它们对文字的态度不一样。
- 展示图不含任何虚构数据：界面上出现的数字（10 款 / 49 色、10/10、5/5）都是真机截图里原有的。
