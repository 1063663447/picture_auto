# 9.4.3 · 款式锁（派生色变卫衣）+ 备用接口模型列表只列有报价的

日期：2026-11-09。前端资源版本不变（9.4.1），后端改动需重启服务。

## 一、飞书改款记录 1382~1390：款式跑偏

**现象**（运营反馈）：3 款式 × 3 颜色的一单，部分成品款式不对 —— 预期长袖，出来是卫衣。

我把这 9 条的成品图从飞书下载下来逐张看（`data/test/feishu-1382-1390/contact-sheet.png`）：

| 记录 | 款式 | 颜色 | 实际 |
|---|---|---|---|
| 1382 | 03 圆领卫衣 | 花灰色 | 圆领卫衣 ✅（基准色） |
| 1383 | 03 圆领卫衣 | 杏色 | **带帽兜+抽绳** ❌ |
| 1384 | 03 圆领卫衣 | 白色 | **带帽兜+抽绳** ❌ |
| 1385 | 23 索罗娜短袖 | 棕色 | 短袖 T ✅ |
| 1386 | 23 索罗娜短袖 | 蓝色 | **帽兜卫衣** ❌ |
| 1387 | 23 索罗娜短袖 | 墨绿色 | 短袖 T ✅ |
| 1388 | 24 长袖成人 | 黑色 | 长袖 T ✅（基准色） |
| 1389 | 24 长袖成人 | 宝蓝色 | **帽兜卫衣** ❌ |
| 1390 | 24 长袖成人 | 墨绿色 | 长袖 T ✅ |

先排除了数据问题：三个款式的 49 张效果图逐张核对（`data/test/effect-audit/*.png`），
颜色和图**全部对得上**，没有错行。

**根因**：款式描述只写在**基准色**的提示词里。派生色（跟随基准只改颜色，`build_master_recolor`）
的提示词由「只改颜色 + 袖口工艺 + 基准/效果图分工」组成，**既没有 `garment.phrase`（"a long-sleeve
cotton-jersey T-shirt…"）、也没有任何 no hood / no drawstrings**。模型只被要求「别动形状、改颜色」，
于是一部分颜色自己发挥成了卫衣（尤其浅色效果图那几张）。

**修法**：新增 **款式锁 `GARMENT TYPE LOCK`**（`prompts.garment_lock()`）：
- 写明目标款式（`garment.phrase`）；
- 按款式特征列出**不许出现的部件**：非卫衣款 → `no hood / no drawstrings / no kangaroo pocket /
  no sweatshirt or fleece body`；平口款 → `no rib-knit cuffs / no rib-knit hem`；
  短袖款 → `no long sleeves`；长袖款 → `no short sleeves`；
- 基准提示词、派生提示词、只换色、两阶段、返工统一注入；
- 否定词同步追加 `Do not add hood, drawstrings, kangaroo pocket, …`；
- 卫衣款式自动**不加** no hood（它是卫衣），靠 slug/phrase 判定。

**真机验证**（故意给个最容易翻车的输入：原图是连帽卫衣，要求改成 24 长袖 T 恤 2 色）：
`terra / gpt-image-2.5-sunburst`，黑色（基准）36.5s、宝蓝色（派生）38.8s，两张都是干净的
长袖 T 恤、**没有帽兜和抽绳**，宝蓝色正是原来错的那一色 → `data/test/garment-lock/`。

回归：`python tools/check_garment_lock.py`（覆盖 11 个款式的基准/派生/否定词、卫衣反例、长短袖互斥）。

## 二、备用接口模型列表：只列有报价的

**现象**：65535 网关有 36 个模型，下拉框全铺出来，其中二十多个没报价（显示「未定价」）。
**要求**：按飞书「模型定价」表来读。

**修法**：`service.list_models()` 默认只保留**能算出单价**的模型（价格解析链：接口 `model_prices`
→ 飞书「模型定价」表 → `pricing.json`），并**始终保留当前正在用的那个**（否则下拉框显示不出当前值）；
新增开关 `defaults.models_only_priced`（默认 `true`）与 provider 级覆盖；
需要看全量时用 `/api/models?provider=terra&include_unpriced=true`。

实测（重启后走 HTTP）：

```
/api/models?provider=terra            → 9 个（隐藏 27）
  gemini-3-pro-image             ¥0.180/张
  gemini-3.1-flash-image         ¥0.150/张
  gemini-nano-banana-2.1         ¥0.150/张
  gpt-image-2                    未定价   ← 当前默认模型，保留
  gpt-image-2.5-sunburst         ¥0.065/张
  gpt-image-2.6-flare            ¥0.038/张
  gpt-image-2.6-sunburst         ¥0.038/张
  grok-imagine-image-2.0         ¥0.100/1K
  grok-imagine-image-quality     ¥0.100/1K
/api/models?provider=a6api            → 1 个（gpt-image-2.5-sunburst，主接口固定价）
```

## 三、验证

- 离线：`check_garment_lock`（新增）+ `selftest / check_effect_ref / check_no_person / check_cuff /
  check_shape_lock / check_primary_workflow / check_garment_priority / check_person_consistency /
  check_artwork_topology` 共 10 个脚本全绿
- 线上：`/api/models?provider=terra` 返回 9 个（隐藏 27）、`/api/gate` 200、`/api/health` 正常
- 付费调用：本轮真机 2 张（¥0.13 报价口径），无其他调用

## 四、遗留

1. 提示词又长了约 450 字符（现在单张 ~11.5KB）。款式锁是必要的，但如果还要继续加约束，
   建议先把「把款式锁写短」和「A/B 验证」一起做，别再堆。
2. 飞书「模型定价」表目前只有 7 行，所以大多数网关模型会显示「未定价」并被列表过滤掉。
   想用某个模型，先在飞书表（或接口 `model_prices`）里给它报价。
3. 主接口 a6api 用 `fixed_unit_price` 覆盖，模型列表天然只有 1 个，不受过滤影响。
