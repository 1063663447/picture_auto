# 9.6.27 · 弹力短袖必须保住罗纹感（派生色不许被磨平成平纹）

## 客户这次说的问题
「弹力短袖需要强调生成出来的衣服要有罗纹感，要不然原图有的、生成出来其他颜色也会被改掉。」

原图和这个款式的效果图都是**罗纹针织**（整件细密竖条），但生成出来——尤其**派生出来的其它颜色**——
变成了普通平纹汗布。原因不在出图参数，在提示词自相矛盾：

    旧文案：「The garment's fabric must match IMAGE 2 (thin cotton jersey …」
            「… the only ribbed part is the neckband」

模型读到「thin cotton jersey」「只有领口有罗纹」，就老实把整件的罗纹去掉了；派生色走的是
「基准成品 → 只换面料颜色」，基准图本身要是已经被磨平，后面每个颜色就更没有罗纹。

## 改了什么
### 1. 款式提示词（`library/prompts/10-stretch-tee.json`）
正文**最前面**插入一条最高优先级的材质规则：

    RIB-KNIT FABRIC TEXTURE — HIGHEST-PRIORITY MATERIAL RULE, it must hold on EVERY colourway.
    … dense, fine vertical ribs … after the recolour they must still be clearly visible, in the same
    pitch, density, depth and direction as IMAGE 2 — on the master image AND on every derived
    colourway of this batch … never flatten the ribs into smooth plain single-jersey …

同时把打架的旧句子换掉：
- `thin cotton jersey … ribbed neckband only` → `fine rib-knit jersey（整件竖条 + 罗纹领口 + 平折边）`
- `the only ribbed part is the neckband` → `袖口下摆没有罗纹**带**（罗纹是面料本身，整件都有）`

负面词补上：不许磨平成平纹、不许换色时丢掉/糊掉竖条、不许改罗纹密度、不许任何派生色失去罗纹。

### 2. 生成器 & 款式库描述（避免下次重建又回退）
- `tools/build_style_prompts.py`：`DETAIL["10-stretch-tee"]` 加同一段（重建也不会丢），NOTES 同步；
- `tools/build_library_from_xlsx.py`：`phrase` 改成 `fine rib-knit jersey (dense vertical ribs …)`，
  `library/styles.json` 同步改（否则 `phrase` 那句还会把模型往平纹带）。

## 验证
### 真机（terra · gpt-image-2.6-sunburst，3 张 ≈ $0.114）
用客户那张白罗纹短袖当原图，跑 **白色（基准）+ 黑色（派生）+ 杏色（派生）**：

| 颜色 | 罗纹 |
|---|---|
| 白色 · 基准 | 整件竖条罗纹清楚，袖口下摆仍是平折边 |
| 黑色 · 派生 | 放大看竖条依然细密连续（之前这里就是平的）|
| 杏色 · 派生 | 浅色下罗纹偏柔但可见，与原图观感一致 |

对照图/特写：`scratch/rib-texture-9.6.27/rib-compare.png`、`*-rib-zoom.png`。

### 离线
新增 `tools/check_rib_texture.py`：四条提示词路径（一次到位 / **派生色** / 只换色 / 两段式第一阶段）
都带罗纹段、旧文案已清、负面词齐全、别的款式不许误带这段、`styles.json` 与 JSON 文案都对得上。
`check_style_prompts.py`（28 款）、`selftest.py` 全绿。

## 备注
- 罗纹是**面料**属性，平口是**工艺**属性：袖口/下摆仍然是无罗纹带的平折边，两者不冲突（提示词里写明了）。
- 以后要调罗纹粗细/疏密，改 `library/prompts/10-stretch-tee.json` 里的 pitch 描述即可；
  如果是别的款式也有罗纹（例如 19 U领T），同样在它的 JSON 里加一段。
