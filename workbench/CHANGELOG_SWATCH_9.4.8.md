# 9.4.8 · 派生色改发「面料色卡」：短袖不再跑成卫衣

日期：2026-11-10。后端改动，需重启服务；前端资源版本不变。

## 现象（飞书改款记录 1526~1542）

17 条记录 = 3 个款式一单（21 童装索罗娜 9 色 / 25 长袖童装 5 色 / 28 卫衣童装 3 色），
目标款式与成品偶尔不一致。逐张核对：

- 1526 `21TZSLN_WH[0]` 白色：短袖 T 恤 ✅（这是**基准色**）
- 1527 `21TZSLN_BK[0]` **黑色：带帽 + 袋鼠兜 + 罗纹收口** ❌（目标应是短袖 T 恤）
- 其余同款各色基本正常

即：**基准色对、个别派生色跑偏**。先排除了数据问题——21 童装索罗娜的 9 张效果图逐张核对
（`data/test/effect-audit/21-kids-sorona-tee.png`）全是短袖 T 恤，颜色也对得上。

## 根因

派生色走「基准成品 + 该色效果图，只改颜色」。效果图虽然是短袖，但它**带完整版型**，
模型偶尔会把效果图的版型也一起搬过来（尤其深色/黑色这类"卫衣先验"强的颜色）。
提示词里写了「IMAGE 2 只作颜色权威」，但**图片本身**仍然提供了一个可抄的形状。

## 修法：派生色改发「面料色卡」

新增 `skillbridge/swatch.py`：从效果图里裁一块 **256×256 的纯面料色卡**
（取衣服主体中心区域放大，避开袖口/领口的阴影高光），按效果图 sha256 缓存。

- **基准色（第 1 色）**：仍然发「原图 + 官方效果图」（版型/工艺/颜色的权威）
- **派生色（第 2..N 色）**：发「基准成品 + 面料色卡（+ 原图人物）」——
  色卡里**没有任何衣服轮廓**，模型只能取颜色，没得抄版型
- 派生提示词换成 `SWATCH_CLAUSE`：明确「IMAGE 2 是一小块面料色卡，只取颜色；
  它不含任何版型，不要从它读剪裁/袖长/帽兜/领型/袖口/下摆」

## 真机验证（复现客户那一单的失败点）

原图用客户那张（小孩穿黑色卫衣的 promo），目标 **21 童装索罗娜（短袖）**，白 + 黑 2 色，
a6api / gpt-image-2.5-sunburst（¥0.038×2）：

| 版本 | 黑色那一张 |
|---|---|
| 修之前（发整张效果图） | 带帽卫衣 ❌（= 飞书 1527） |
| 9.4.8（发面料色卡） | **短袖圆领 T 恤 ✅** 无帽兜、无袋鼠兜 |

成品：`data/test/style-drift/swatch-white.png`、`data/test/style-drift/swatch-black.png`。

## 回归

- 新增 `tools/check_swatch.py`：色卡尺寸/缓存；派生提示词含色卡说明、基准提示词仍是官方效果图；
  真实请求里「基准发效果图、派生发 256² 色卡（+ 原图在最后）」。
- 同步更新了三处旧契约断言（它们还在断言「派生第 2 张 = 整张效果图」）：
  `check_effect_ref.py`、`check_shape_lock.py`、`check_garment_priority.py`、`check_person_consistency.py`。
- **全套 16 个离线脚本通过**：selftest / check_swatch / check_hood_cords / check_cuff /
  check_request_boundary / check_shape_lock / check_effect_ref / check_garment_lock /
  check_garment_priority / check_person_consistency / check_artwork_topology / check_no_person /
  check_primary_workflow / check_model_params / check_multi_style / check_thumb_package。

## 说明

- 色卡是「按效果图算出来的」，效果图换颜色/换款会自动重新生成（按 sha256 缓存）。
- 已经出错的旧成品不会自动重画；在成品上点「改图」或者整单重跑即可。
- 如果以后出现「花的、渐变的面料」被色卡抹平的情况，可以把 `swatch.CROP_BOX` 调小一点
  （裁更靠中心的一块），或直接退回整张效果图（把 `swatch=False`）。
