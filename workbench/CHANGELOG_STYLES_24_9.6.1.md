# 9.6.1 · 口径修正：这张表就是 24 个款式（去掉成人牛角袖和裤子）

日期：2026-11-11。数据 + 版本号改动，**需重启服务**；前端资源不变（`?v=9.6.4`）。

客户原话：

> 这里面总共应该是24个款式，并没有什么成人牛角袖和裤子

## 一、核对结果

工作簿里确实有 27 个款式 sheet，但「目录」页只排了 **24 格**（23 个不同代号 + 06 重了一格），
客户点名的「成人牛角袖」和「裤子」都不在目录里：

| 代号 | 款式 | 目录里有吗 | 处理 |
|---|---|---|---|
| 14 | 成人牛角袖 | 没有 | **移出款式库** |
| 15 | 中短裤 | 没有 | **移出款式库** |
| 16 | 运动短裤 | 没有 | **移出款式库** |
| 01 | 纯棉短袖 | 没有（表内注明已被 23 取代） | 保留（有 sheet 和效果图，客户没要求移） |
| 09 | 童装纯棉 | 没有（表内注明已被 21 取代） | 保留（同上） |
| 30 | 扎染T（30#ZRT） | **有** | 工作簿里没有它的 sheet（无颜色行/效果图），暂无法建档 |

所以版本库现在是 **24 款 / 112 色**（原来 27 款 / 121 色，减掉 14 的 5 色 + 15 的 2 色 + 16 的 2 色）。

## 二、改动

- `tools/import_ph_catalog.py` 新增 `EXCLUDE_CODES = {"14", "15", "16"}`：
  导表时跳过这三张 sheet，写回 `styles.json` 时也会把它们剔掉 —— **重跑导表不会再回来**。
  另记 `MISSING_SHEET_CODES = {"30": "30扎染T"}`，提醒目录里有、工作簿没有 sheet 的那一款。
- 新增 `tools/prune_styles_24.py`：一次性清理（可 `--dry-run` 先看），
  把这三款连同 `library/effects/<slug>`、`library/refs/<slug>`、`library/prompts/<slug>.json` 一起移出。
  **备份在 `scratch/pruned-styles-9.6.1/`**（含 styles.json 原样、图、提示词），要恢复直接拷回。
- `library/styles.json`：24 款 / 112 色；品类只剩 `top` / `vest`（`bottom` 的代码能力保留，
  这批没有裤子，`check_catalog` 里 `BOTTOMS` 置空、并把三款列为「不许出现」）。
- `library/prompts/`：删掉 `14-dolman-tee.json` / `15-mid-shorts.json` / `16-sports-shorts.json`，
  索引重建为 **27 份**（24 业务款 + 3 个 skill 通用款）。
- 断言与文档口径同步：`selftest`（24 款 / 112 色 / 品类 top+vest）、`check_catalog`、
  `check_cuff`（`EXPECTED_BOTTOM` 置空）、`verify_portable_9_6_0.py` → 9.6.1（24/112/27 份），
  README / HANDOVER / CHANGELOG 数字全部改齐。

## 三、自检

```powershell
python tools/import_ph_catalog.py --dry-run   # 24 款 / 112 色，14/15/16 会被 skip
python tools/prune_styles_24.py --dry-run     # 只剩「无可移除」时即为干净
python tools/check_catalog.py                 # 24 款 / 112 色 / 27 份提示词
python tools/selftest.py
```

## 四、如果客户说的 24 款是另一种组合

「目录」里的 24 格还包括 **30 扎染T**（表格里没有它的 sheet、没有颜色和效果图），
同时把 01 纯棉短袖、09 童装纯棉 剔在目录之外。现在的做法是：
**保留 01/09（有 sheet、有图），去掉 14/15/16（客户明确说没有）**，结果同样是 24 款。
如果实际要的是「目录那 24 个」，需要客户补 30 扎染T 的 sheet（颜色 + 效果图），
再把 01/09 从 `EXCLUDE_CODES` 之外的人工清单里移出；改起来是改数据，不动代码。
