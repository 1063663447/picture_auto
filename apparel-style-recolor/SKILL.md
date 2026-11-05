---
name: apparel-style-recolor
description: "将一张或多张服装图片改为指定的圆领卫衣、连帽卫衣或短袖，并按该类型内置颜色参考图逐色生成版本；保留人物、构图和原服装图案内容，按图片分目录打包为 ZIP。用于其它生图模型的图片编辑工作流，不负责调用特定图片生成 API。"
---

# 服装改款换色

把以下规则作为图片编辑模型的执行规范。用户上传一张或多张服装图片，并指定目标服装类型后，逐张独立处理。不要混合不同图片中的人物、背景、姿势、服装图案或饰品。

## 支持类型与颜色参考

本 skill 自带目标类型的颜色参考图，全部位于 `assets/color-references/`。必须先按用户指定的目标类型选择唯一目录，再仅使用该目录内的颜色参考图。完整映射和图片清单见 [references/color-reference.md](references/color-reference.md)。

支持以下目标类型：

- `圆领卫衣`、`圆领套头卫衣`、`crewneck sweatshirt` -> `crewneck-sweatshirt/`
- `连帽卫衣`、`帽衫`、`hoodie` -> `hoodie/`
- `短袖`、`短袖T恤`、`T恤`、`short-sleeve` -> `short-sleeve/`

用户未指定目标类型时，先要求用户选择类型，不要自行猜测。用户指定某个颜色或参考图文件名时，只生成对应颜色版本；未指定时，使用目标类型目录中的全部参考色。

颜色判断必须直接查看参考图片中衣服主体，不要仅根据文件名或模型记忆猜测。颜色参考图只用于确定目标服装的颜色，不得复制其中的人物、背景、版型、姿势或构图。

## 工作流程

### 1. 读取原图和目标类型

- 接收用户上传的一张或多张服装图片，以及目标服装类型。
- 从用户文字中识别圆领卫衣、连帽卫衣或短袖，选择对应的内置颜色参考目录。
- 读取选定目录中的全部参考图并按文件名排序，为每张图建立颜色标签，例如 `black`、`heather-gray` 或 `color-01`。
- 将每张上传图片默认视为一个独立 item；用户明确说明多张图片是同一件衣服的不同角度时，才将它们归为同一 item。
- 若生图模型支持多图参考或局部重绘，**第一张永远是客户原图**：人物/平铺场景、机位、光线和原有图案都以它为准；**第二张是该颜色的官方效果图**（款式表里的白底平铺产品图）：版型、肩线、衣宽、下摆、袖长袖型、领型、罗纹/平口工艺、面料垂感和精确布色都以它为准。
- 效果图只贡献「这件衣服长什么样、是什么颜色」，**绝不复制它自己的印花、文字、logo、人物、白底或道具**；如果原图有真人就保留原来那个人，原图是空衣服就保持空衣服。
- 同款多色出图时，先按上面两条出**第一个颜色**作为基准，其余颜色**以基准成品 + 该色效果图**为参考只换面料颜色，保证同款各色版型一致。

### 2. 改为目标服装类型

以每张原图为基础，将可见服装改成用户指定的目标类型。改款必须真实、完整地发生，不只是改变颜色或在原衣服上添加局部特征。

- 目标为圆领卫衣时：生成圆领、无帽、长袖、卫衣面料和自然的罗纹领口、袖口及下摆；不能残留帽子、抽绳、拉链或短袖结构。
- 目标为连帽卫衣时：生成完整连帽、自然帽檐、抽绳、长袖卫衣结构和罗纹袖口及下摆；帽子应与人物头部、头发、肩部和姿势自然衔接，不能遮挡脸部或产生多余帽子。
- 目标为短袖时：生成自然短袖袖长、T 恤领口和轻薄针织面料；不能残留长袖、连帽、抽绳、卫衣罗纹袖口或厚重卫衣材质。
- 目标为长袖 T 恤时：生成自然长袖、T 恤领口和针织面料；**袖口与下摆为同布平车折边（平口）**，袖筒到边保持原宽度、只把布边折进去一道窄边；不能出现卫衣罗纹袖口、罗纹下摆、松紧收口或拼接袖口——罗纹只属于卫衣，长袖 T 恤上只有领口是罗纹。
- 保留原服装上可见的图案、文字、数字、logo、刺绣、贴花、印花和装饰元素。改款后将这些内容自然放置在新服装对应的可见区域，保持原有文字可读、图案主体和相对布局，不删除、不新增、不用其它内容替换。
- 若原图图案跨越会在目标款式中消失的部位，例如长袖变短袖后被裁掉的袖段，只保留目标服装可见区域内合理的原始内容；不要为补齐图案而凭空扩展或重绘新图案。
- **主体由原图决定，不许自行添加**：原图是人物上身（或局部上身）时，保留人物身份、脸部、发型、皮肤、手部、饰品、手表、裤子、鞋子、背景、床品、墙面、道具、镜头角度、裁切、景深、分辨率和整体光线；原图是平铺/挂拍/隐形模特（没有真人）时，**成品里也只能有这件衣服**——不得添加模特、脸、头、头发、脖子、手臂、手、腿、脚或任何皮肤，领口露出的是背景或领内暗部，袖筒和衣身是空的。
- 原图有真人时保留姿势与身体比例，衣服肩线、袖窿、领口、下摆和褶皱必须与人物姿势一致；原图是平铺时褶皱与走向必须与平铺的原始形态一致。两者都不得出现多余肢体、扭曲手指或不自然的衣服边界。

### 3. 按目标类型换色

完成目标款式转换后，为每个选定颜色参考图单独生成一个版本：

- 只改变新服装面料的基础颜色，精确匹配对应参考图中的色相、明度、饱和度和冷暖关系。
- 颜色变化必须遵循衣服原有的高光、阴影、褶皱、接缝和织物纹理，不能做成平面纯色块。
- 原始图案、文字、logo、刺绣和装饰在换色后必须仍然完整清晰。除非图案本身包含底色区域，不要把面料底色直接涂到图案上。
- 对黑色、深色或低饱和色，仍要保留可见的面料层次和图案细节；对浅色和白色，不得把颜色溢到皮肤、头发、裤子、饰品或背景。

## 推荐提示词

其它生图模型若支持图像编辑、局部重绘或遮罩，应优先采用这些模式。以原图作为主参考，以选定目标类型的内置颜色参考图作为颜色参考。

**改款换色提示词：**

> Transform only the visible garment into a [TARGET_GARMENT_TYPE] and recolor its fabric base to match the selected bundled color reference image, including hue, brightness, saturation, and temperature. Preserve every visible original print, text, number, logo, embroidery, sticker, graphic, decorative artwork, and label; keep it readable, recognizable, and naturally positioned on the new garment. Make the garment construction physically correct for [TARGET_GARMENT_TYPE]. The source image alone decides what the subject is, so keep it exactly: when the garment is worn, keep the same person, face, hair, skin, hands, jewelry, watch, pose and body proportions; when the garment is shown on its own (flat lay, hanger, invisible or ghost mannequin), keep it exactly that way — empty and unoccupied. Keep the same background, props, camera angle, framing, lighting, and image quality, and never introduce a person, a face, hands, skin, a body or a prop that the source does not already contain. The color reference controls only garment color and must not introduce its own person, pose, background, composition, or garment design.

替换 `[TARGET_GARMENT_TYPE]`：

- 圆领卫衣：`crewneck sweatshirt with a ribbed crew neckline, long sleeves, ribbed cuffs, and ribbed hem, with no hood, drawstrings, or zipper`
- 连帽卫衣：`hoodie with a natural hood, drawstrings, long sleeves, ribbed cuffs and a ribbed hem, and sweatshirt fabric`
- 短袖：`short-sleeve T-shirt with a natural short sleeve length, T-shirt collar, and lightweight knit fabric`
- 长袖 T 恤：`long-sleeve cotton-jersey T-shirt with flat plain cuffs and a straight flat hem, ribbed neckband only, and no sweatshirt ribbing`

## 负面约束

将以下内容作为负面提示词或编辑限制；若模型不支持负面提示词，将其合并到主提示词末尾：

> Do not change the subject the source shows: a worn garment keeps the same person, face, hair, skin tone, hands, fingers, jewelry, watch, pants and shoes, while a garment photographed on its own stays empty. Do not change furniture, bedding, wall, background, lighting direction, camera angle, crop, pose, body shape, image composition, or non-garment pixels. Never add a person, model, face, head, hair, neck, hands, arms, legs, feet or skin where the source shows only the garment. Do not remove, erase, cover, blur, simplify, replace, or add any print, text, number, logo, embroidery, sticker, label, pattern, or decorative artwork. Do not leave the original garment type features when converting to the selected type: no hood, drawstrings, zipper, or short sleeves on a crewneck sweatshirt; no original long sleeves, hood, drawstrings, or sweatshirt cuffs on a short-sleeve T-shirt. Do not get the cuff and hem construction wrong either: a T-shirt (short or long sleeve) must keep plain, flat, self-fabric folded cuffs and hem — never a rib-knit band, elastic band, gathered cuff, or sweatshirt ribbing — while a sweatshirt or hoodie must keep real rib-knit cuffs and a rib-knit hem. Do not add extra limbs, altered fingers, warped anatomy, extra hoods, new objects, background replacement, beauty retouching, or color spill outside the garment.

## 输出要求

- 每张输入图都先完成指定服装类型转换，再为每个选定颜色参考图输出一张最终成品。
- 最终成品数量等于“输入图片数量 x 当前目标类型选定的颜色参考数量”。例如：1 张图改为圆领卫衣并使用全部 5 个参考色，应输出 5 张图片；2 张图改为连帽卫衣并使用全部 3 个参考色，应输出 6 张图片。
- 输出保持与原图相同的宽高比例；除非用户另有要求，不主动裁切、放大、缩小或改变画质。
- 默认创建包含目标类型标识的总目录，例如 `crewneck-sweatshirt-style-recolor-batch/`。不同目标类型不得混在同一个总目录。
- 总目录下每张上传图各自建立一个子目录，例如 `item-01/`、`item-02/`。
- 每个子目录只放该原图的最终成品，例如 `item-01_black.png`、`item-01_heather-gray.png`。
- 如同一 item 有多个输入视角，使用 `view-01`、`view-02` 区分文件名，但仍只放在该 item 的子目录中。
- 所有选定颜色和所有输入图片完成后，将总目录整体压缩为同名 ZIP，例如 `crewneck-sweatshirt-style-recolor-batch.zip`。
- 打包时检查每张最终图片：小于 `2,000,000` 字节时原样保留；大于或等于该限制时压缩到严格小于 `2,000,000` 字节后再放入总目录和 ZIP。
- ZIP 内必须保留总目录和每张图片的子目录层级。
- 优先使用 [scripts/package_outputs.py](scripts/package_outputs.py) 完成目录校验、图片大小控制和 ZIP 打包。详细规则见 [references/output-packaging.md](references/output-packaging.md)。
- 完成后逐张核对：服装类型是否正确、颜色是否匹配参考、原始图案和文字是否完整、衣服边界与人体是否自然、皮肤和背景是否被误改、褶皱和光影是否自然；**原图是纯衣服平铺的，成品里不能出现人物或身体部位**（出现了就重画该张）。
- 如果某张图无法同时满足“目标款式正确”和“保留原图图案”的要求，保留原图并报告具体问题，不要用大范围重绘掩盖问题。
