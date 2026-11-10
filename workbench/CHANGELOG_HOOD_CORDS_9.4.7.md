# 9.4.7 · 帽绳改成「同色系」（修正 9.4.6 的理解偏差）

日期：2026-11-10。后端改动，需重启服务；前端资源版本不变。

## 澄清

客户上一轮说「连帽卫衣的带子容易串」，我理解成**绳子交叉/压图案**，于是 9.4.6 加了一套几何约束
（不交叉、不成 X、不打结、不穿印花）。客户这次说明：指的是**颜色串色** ——
**黑卫衣生成出白帽绳**，绳子「跳出衣身色系」。

## 处理

1. **回退几何条款**：`DRAWSTRING_CLAUSE` 里关于交叉 / 成环 / 打结 / 穿图案 / 第三条绳的那一套全部撤掉。
2. **改成色系约束**，并放到提示词**靠前**位置（转换段之后、skill 模板之前）：

   > HOOD CORD COLOUR — the two hood cords must be the SAME colour as THIS hoodie's fabric：
   > 黑/炭灰卫衣 → 黑/炭灰绳；白/米白卫衣 → 米白绳；藏青卫衣 → 藏青绳。
   > 绳子是这块面料本身裁出来的，共享色相、明度区间、明暗与阴影；只保留照片里的绳宽、纹理和绳头。
   > **源图那根绳的颜色属于它自己的旧色系，必须被本批次的颜色替换，不许照抄**；
   > 源图黑卫衣上是白绳的话，白绳要变成深色面料色；本批每一色各跟自己的衣身走，不跟源图、也不跟别的色。

3. 否定词同步改为色系版：
   `Do not give the hood cords a colour from outside the garment's colour family, do not put white or
   silver cords on a dark hoodie, do not put charcoal cords on a light hoodie, do not borrow the cord
   colour of another colourway, do not make the cords glossy or metallic, do not leave the cords
   uncoloured while the garment is recoloured.`
4. 注入位置不变（一次到位 / 派生色 / 只换色 / 两段式第一阶段），仍然只对连帽款生效；
   非连帽款继续写「no drawstrings」。

## 真机验证（客户那张黑卫衣当原图 → 06 连帽卫衣 · 黑色，a6api ¥0.038×2 轮）

| 版本 | 帽绳 |
|---|---|
| 原图 / 修之前 | **白色**（跳出黑色衣身色系） |
| 9.4.6（几何约束） | 还是白色（说明问题不在交叉） |
| 9.4.7（色系约束 + 前置） | **黑色**，与衣身同色系、同明暗 ✅ |

对照：`data/test/hood-cords/hoodie-cords-samecolour-v2.png`（修后）、
`hoodie-cords-samecolour.png`（第一版色系写法，仍在后面 → 无效，说明位置也重要）。

## 回归

- `tools/check_hood_cords.py` 断言改成色系版：连帽款四条路径都必须含
  `HOOD CORD COLOUR` / `SAME colour as THIS hoodie's fabric` / 黑→黑绳、白→米白绳的对照 /
  「源图绳色是旧色系必须替换」；负面词必须含「跳出衣身色系」「黑卫衣白绳」「串到别的颜色」；
  非连帽款（5 个）不许出现该条款且必须写 `no drawstrings`。
- 全套离线脚本全部通过。

## 经验

1. **先对齐理解再写条款**：同一个词（"串"）可以指几何也可以指颜色，写提示词前先确认现象；
   这次的教训是不要凭截图自己补戏。
2. **条款位置影响生效**：同样的色系要求放在 13KB 提示词的尾部时模型不理，
   挪到转换段之后（前 1KB）立刻就对了 —— 重要约束尽量前置。
3. 源图特征（白帽绳）会被模型当"设计"保留，所以必须明确写「源图的那份颜色是旧色系，要替换」。
