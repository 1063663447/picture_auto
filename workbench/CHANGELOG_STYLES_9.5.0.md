# 9.5.0 · 菲律宾款式表整合：每款一套专属 skill（口径在 9.6.1 修正为 24 款）

日期：2026-11-11。后端 + 前端 + 数据改动，**需重启服务**；前端资源版本 `?v=9.5.0`（Ctrl+F5）。

## 背景

客户提供《菲律宾-服装款式代号表》（74.8 MB，`sha256 afbf61a0…`）：**27 个款式 sheet（客户口径为 24 款）+ 目录**，
每个款式每张颜色一张官方平铺效果图，共 **112 个色号**。要求「整合到每个款式的 skill 里去」。

原来款式库只有 10 款 49 色，且提示词层**只按「上衣」建模**（袖长 + 袖口两个维度）。
这份表里有背心、挂脖、短裤，直接套上衣的句子会自相矛盾（背心没有袖子、短裤没有袖口），
模型会照着「补一只袖子 / 画成 T 恤」。

## 一、数据：24 款 / 112 色（9.6.1 修正后口径）

新增 `tools/import_ph_catalog.py`，一次把整张表吃进款式库：

- 旧款式（10 个）**补色号**：03 补红、06 补杏色+酒红、07 补紫、28 补杏色+酒红等；
- 新款式（17 个）**建档**：`slug` / `display_name` / `name_en`（`16YDDK`）/ `aliases` /
  `phrase` / `fit` / `cuff` / `sleeve` / `kind` / `notes`；
- 每色效果图 → `library/effects/<slug>/<色号>.jpg`（原图，用作版型 + 颜色的权威参考）；
- 每色缩略图 → `library/refs/<slug>/<label>.jpg`（长边 900，界面点色 + 兜底参考图）；
- `hex` 由效果图面料区取样（`fabric_hex`），界面色块不再靠手填；
- 重跑**幂等**：`--dry-run` 只打印计划，正式跑会按代号合并、只补缺的色。

新款式清单：

| 代号 | slug | 品类 | 结构要点 |
|---|---|---|---|
| 01 | `01-cotton-tee` | 上衣 | 纯棉哑光宽松短袖（已被 23 取代，`replaced_by` 标注） |
| 04 | `04-milk-silk-tee` | 上衣 | 牛奶丝：丝光 + 垂坠，不能画成哑光棉 |
| 05 | `05-cropped-tee` | 上衣 | 露脐短款（已被 26 取代） |
| 08 | `08-tank-top` | 背心 | 宽肩带高袖笼、深圆领、衣长偏短 |
| 09 | `09-kids-cotton-tee` | 上衣 | 童装纯棉（已被 21 取代） |
| 11 | `11-kids-dolman-tee` | 上衣 | **插肩双色**：身子白、袖子和领口才是色号色 |
| 12 | `12-curved-hem-tee` | 上衣 | 弧形下摆（中间低两侧高）+ 宽领口 |
| 13 | `13-mens-tank` | 背心 | 宽肩带、罗纹圆领、宽松 |
| 14 | `14-dolman-tee` | 上衣 | 同 11，成人版 |
| 15 | `15-mid-shorts` | **下装** | 松紧腰 + 抽绳 + 侧袋，及膝以上 |
| 16 | `16-sports-shorts` | **下装** | 短裤腿 + 圆抽绳 + 侧缝/裤脚白色撞色滚边 |
| 17 | `17-kids-cropped-tee` | 上衣 | 童装短款露脐 |
| 18 | `18-halter-tank` | 背心 | 绕颈挂脖、露肩背、修身 |
| 19 | `19-u-neck-tee` | 上衣 | 深宽 U 领 + 修身罗纹 |
| 20 | `20-distressed-tee` | 上衣 | 酸洗做旧：颜色刻意不均，不能抹平 |
| 26 | `26-cropped-sorona-tee` | 上衣 | 方版露脐短款索罗娜 |
| 31 | `31-ink-wash-tee` | 上衣 | 面料本身是水墨/大理石晕染 |

另外 03/06/07/10/21/23/24/25/28/29 十个老款式刷新为菲律宾表的效果图与色号（原图版本替换）。

## 二、新增「品类」维度（`kind`）

`skillbridge/skill.py`：

```python
SLEEVE_NONE = "none"                                  # 背心/无袖/短裤
KIND_TOP / KIND_VEST / KIND_BOTTOM                    # 上衣 / 背心 / 下装
infer_sleeve() / infer_kind()                         # 从 slug/别名/描述推断
GarmentType.kind / .kind_zh / .is_bottom / .is_sleeveless
```

`skillbridge/prompts.py` 按品类**整段替换**构造约束，而不是硬套上衣：

| 段落 | 上衣 | 背心（vest） | 下装（bottom） |
|---|---|---|---|
| 袖长/部位 | `SLEEVE_LONG/SHORT_CLAUSE` | `SLEEVE_NONE_CLAUSE`（袖笼包边 + 肩带，没有袖子） | `LEG_SHORTS_CLAUSE`（腰头 + 裆 + **两条腿**，不是上衣） |
| 袖口/下摆 | `CUFF_PLAIN*/RIBBED` | `CUFF_HEM_VEST_CLAUSE`（袖笼 + 领口 + 直下摆） | `CUFF_HEM_BOTTOM_CLAUSE`（松紧腰 + 裤脚，滚边按产品来） |
| 款式转换段 `strip` | 去罗纹/补罗纹 | 「把袖子整段剪掉，重建袖笼和肩带」 | 「整件上衣去掉，改做短裤」 |
| 禁用件 `garment_forbidden` | no hood / no drawstrings / 罗纹… | + no sleeves / no cap sleeves | 只禁上衣部件、长裤、裙子（**不再禁抽绳**，15/16 本身就有抽绳） |

## 三、17 个款式的「结构细节段」

`tools/build_style_prompts.py` 新增 `DETAIL` / `DETAIL_NEGATIVE`：`phrase` 里说不清的
结构必须单独点名（生成的 `positive` 里紧跟款式转换段，位置靠前模型才听）。

例（11/14 插肩双色）：

> RAGLAN TWO-TONE — this product is deliberately TWO-TONE … the front and back body panels stay
> WHITE / off-white, while the raglan sleeves and the crew neckband take the colourway colour …
> Do not flood the whole garment with the colourway colour and do not make it all white.

`DETAIL` 覆盖：插肩双色（11/14）、露脐（05/17/26）、弧形下摆（12）、U 领（19）、挂脖（18）、
水墨面料（31）、酸洗做旧（20）、牛奶丝丝光（04）、纯棉哑光（01/09）、
中短裤（15）、运动短裤白色滚边（16）。

## 四、顺手修掉的毛病

1. **款式转换段的老病句**：`it must not gain no hood, no drawstrings…`
   字面意思是「必须长出帽兜」。改成 `none of these may appear on it: no hood, …`。
   27 份提示词全部重生成（老款式只改动这一句）。
2. **改图的原始 brief 会重复宣布图片角色**：`_sanitize_brief()` 现在还会剥掉上一轮的
   `REFERENCE ROLES / RESHAPE / 版型锁`（改图附的是「当前成品 + 原始上传」，没有产品图）。
3. **袖子/袖口相关的三个离线断言脚本过期**：
   - `check_effect_ref.py`：基准色改成按「被选中的颜色」取效果图（色号顺序随新表变了）；
   - `check_cuff.py`：按品类分支（背心/短裤不再要求「平折边」「短袖子句」）；
   - `check_composition_preservation.py`：派生色第 2 张图改为断言「面料色卡」（9.4.8 起的真实行为）。

## 五、界面

- 目标款式下拉按品类分组：`<optgroup label="上衣（21）">` / `背心（3）`，24 款不再平铺一长条；
- 款式卡片的工艺标：背心显示「**无袖**」、短裤显示「**下装**」（以前统一显示「平口」，容易被理解成要罗纹）；
- `/api/skill` 的每款新增 `kind` / `kind_zh`。

## 六、自检

```powershell
python tools/import_ph_catalog.py --dry-run     # 导表计划（24 款 / 112 色）
python tools/build_style_prompts.py --list      # 30 款提示词覆盖情况
python tools/check_catalog.py                   # 本次整合的七组断言
python tools/show_prompt.py 16-sports-shorts black   # 人工复核某一款的最终提示词
python tools/selftest.py                        # 主自检（含 24 款 / 112 色 / 每色有效果图）
```

离线脚本全绿：`check_catalog` / `check_style_prompts` / `check_cuff` / `check_effect_ref` /
`check_swatch` / `check_no_person` / `check_shape_lock` / `check_garment_lock` /
`check_garment_priority` / `check_request_boundary` / `check_hood_cords` / `check_model_params` /
`check_artwork_topology` / `check_pattern_mode` / `check_person_consistency` /
`check_composition_preservation`（19 tests, 1 skipped）/ `selftest`。

## 七、已知限制

- **18 挂脖背心**的官方效果图是**模特架上的实拍**（白色挂脖、有人台和背景道具）。
  提示词里加了「只画衣服，不画人台/背景」，但这是唯一一张带载体的效果图 —— 出图若带人台，
  按「无载体」复查返工即可；后续换成平铺图会更稳。
- **11 / 14 牛角袖是双色款**：色号指的是**袖子 + 领口**的颜色，身子恒为白色。
  这是产品本身的设计（表里 5 个色号的图都是白身彩袖），提示词已写死双色分工；
  若客户以后出「整件同色」版本，需要另立款式。
- **01 / 09 / 05** 在表里标注「部分尺码已停产，替代款见 23 / 21 / 26」，
  已在 `styles.json` 里写 `replaced_by` 与 `notes`，仍可正常出图（效果图沿用替代款的产品图）。
- 效果图导入后 `library/effects/` 共 112 张（约 37 MB，进仓库）；原始 xlsx 74 MB 不进仓库
  （`library/source/*.xlsx` 已 gitignore，重跑导表脚本即可）。
