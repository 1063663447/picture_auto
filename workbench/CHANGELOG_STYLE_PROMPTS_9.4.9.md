# 9.4.9 · 提示词拆分：每个款式一套专属默认提示词

日期：2026-11-10。后端改动，需重启服务；前端资源版本不变。

## 为什么拆

之前所有款式共用一套「按属性拼装」的逻辑（袖长/袖口/帽兜分支）。问题：

- 改 06 连帽卫衣的写法会连带影响 03 圆领卫衣（同属"螺纹 + 长袖"分支）；
- 每个款真正需要的细节不同（落肩幅度、罗纹宽度、帽绳、下摆工艺），属性分支表达不了；
- 后面还要加十几个款式，属性组合会爆炸。

## 拆成两层

| 层 | 位置 | 内容 |
|---|---|---|
| **共享骨架** | 代码 `prompts.py` | 图案锁（PRINT LOCK）、材质锁、效果图/面料色卡分工、主体与禁人物规则、结构收尾（keep/negative 合并） |
| **款式专属核心** | `library/prompts/<slug>.json` | `positive`：这个款式怎么拆掉源图特征、怎么建目标款；`negative`：这个款式专属的否定词 |

文件格式固定四字段：

```jsonc
{
  "slug": "06-hoodie",
  "notes": "连帽卫衣：帽子+帽绳（绳与衣身同色系）、袋鼠兜、罗纹袖口下摆",
  "positive": "CONVERT THE GARMENT TYPE … GARMENT TYPE LOCK … SLEEVE LENGTH … CUFF & HEM … HOOD CORD COLOUR …",
  "negative": "Do not replace the ribbed cuffs … Do not cross … "
}
```

已生成 **13 份**（10 个业务款式 + skill 自带的短袖/圆领卫衣/连帽卫衣），
索引 `library/prompts/index.json` 列出每份的字数与备注。

## 代码改动

- 新增 `skillbridge/style_prompts.py`：读写款式级提示词（`load_all` / `load_style_prompt` / `save_style_prompt` / `write_index`）
- `GarmentType` 增加 `style_positive` / `style_negative`，`library.load_library()` 加载时挂上
- `prompts.py` 新增 `style_positive()` / `style_negative()`：
  **有专属文件就用它，没有就回落到原有的「按属性拼装」** —— 新款式可以先跑起来再补文件
- 四条路径（一次到位 / 派生色 / 只换色 / 两段式第一阶段）和负面词都走这两个入口

## 工具

```powershell
python tools/build_style_prompts.py --list        # 看哪些款式有 / 没有专属提示词
python tools/build_style_prompts.py --all         # 按当前逻辑生成/刷新全部（已存在则跳过）
python tools/build_style_prompts.py --all --force # 覆盖（先备份 .bak）
python tools/build_style_prompts.py --new 24-cxcr # 新款式只生成一份
```

## 以后新增一个款式（三步）

1. `library/styles.json` 加这个款式（代号、`name_en`、`phrase`、`cuff`、`sleeve`、`fit`、颜色 + `effect`）
2. `python tools/build_style_prompts.py --new <slug>` 生成它的专属提示词骨架
3. 按需要改 `library/prompts/<slug>.json` 的 `positive` / `negative`（改哪个款式就只影响哪个款式），
   然后 `python tools/check_style_prompts.py` + `python tools/selftest.py`

## 验证

- 新增 `tools/check_style_prompts.py`：覆盖（13/13）、生效（专属文本出现在最终提示词与负面词里）、
  隔离（改 A 不影响 B）、新增（能落盘/读回、字段固定）、回退（删掉文件仍能按属性拼装）
- 全套 **17 个离线脚本通过**（含 swatch / hood_cords / cuff / garment_priority / person_consistency 等）
- 每个款式生成前后提示词长度基本不变（内容等价，只是从"代码拼"变成"文件存"），
  也就是说这次拆分**不改变出图行为**，只把可编辑面打开。
