# 9.4.6 · 连帽卫衣的帽绳不再「串」（交叉/打结/穿图）

日期：2026-11-10。后端改动，需重启服务；前端资源版本不变。

## 现象

客户反馈：连帽卫衣生成后**两条帽绳容易串** —— 互相交叉成 X、打结、从错误的位置钻出来，
甚至整条绳子压在印花上、和图案轮廓糊在一起（客户在图上用红框标出的就是这一块）。

## 修法

新增 **帽绳约束 `HOOD DRAWSTRINGS`**（`prompts.DRAWSTRING_CLAUSE`），只对连帽款注入：

- 帽子有 **正好两条** 绳，分别从左右两个对称的鸡眼扣（金属/塑料）出来；
- 两条绳**同方向垂下、并排平行、保持间距**，靠重力自然微弯；
- 明确禁止：交叉、成 X、成环、打结、互相缠绕、塞回帽子里、拉到脸前、
  **穿进印花里**、与图案轮廓合并、多加第三条绳、把绳系到口袋或袖子上；
- 绳子的颜色/宽度/质感沿用原图，两端是同高度的干净绳头（金属、塑料或热切封口）；
- 印花必须连续可读：绳子最多作为一条细线自然**压在**印花前面，不能切开或替代图案线稿。

同时追加对应否定词（`prompts.drawstring_negative()`）：
`Do not cross the two hood cords… form an X, a loop or a knot… thread a cord through the printed artwork… add a third cord…`

注入位置：**一次到位、派生色（跟随基准）、只换色、两段式第一阶段** 四条路径都覆盖，
并且只在连帽款生效 —— 非连帽款继续写「no drawstrings」（`GARMENT TYPE LOCK` 已有）。

## 真机验证（a6api / gpt-image-2.5-sunburst，¥0.038）

拿客户那张黑卫衣当原图，重新出一张 06 连帽卫衣 · 黑色：

- 修之前：两条绳在领口上方交叉成 X，压着圣诞树的星星
- 修之后：两条绳从左右鸡眼扣垂下、**并排平行、互不交叉、不碰图案**
  → `data/test/hood-cords/hoodie-cords-fixed.png`

## 回归

- 新增 `tools/check_hood_cords.py`：连帽款四条路径都必须含帽绳约束（含 "exactly TWO cords"、
  "must NOT cross each other"、"not threaded through the print" 等 8 条断言）+ 负面词必须含交叉/成 X/穿图；
  非连帽款（5 个款式）反过来不许出现帽绳约束、且必须写 "no drawstrings"。
- 全套 15 个离线脚本通过：selftest / check_cuff / check_hood_cords / check_request_boundary /
  check_shape_lock / check_garment_priority / check_person_consistency / check_artwork_topology /
  check_effect_ref / check_no_person / check_garment_lock / check_primary_workflow /
  check_model_params / check_multi_style / check_thumb_package。

## 说明

- 已有错误成品不会被自动重画；要修就在成品上点「改图」重跑（现在改图也会带上帽绳约束）。
- 帽绳和「罗纹袖口」一样属于**必须单独钉住的结构细节**：模型默认会自由发挥。
  以后遇到类似的固定部件（拉链、抽绳、口袋、领标）也按这个套路加独立条款 + 否定词。
