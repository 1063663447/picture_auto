# 目标类型与颜色参考图规则

## 使用原则

本 skill 的颜色参考图已经复制到 `assets/color-references/`，使用时不需要访问 `C:\TK-图`。外部目录只是素材来源，skill 内副本才是交付工作流的固定参考。

先识别用户指定的目标服装类型，再只读取对应目录内的图片。参考图只确定服装颜色；人物、背景、版型、姿势和构图不能复制到用户图片中。

## 类型映射

| 用户指定类型 | skill 内部目录 | 参考色数量 | 来源目录 |
|---|---|---:|---|
| 圆领卫衣、圆领套头卫衣、crewneck sweatshirt | `assets/color-references/crewneck-sweatshirt/` | 5 | `C:\TK-图\圆领卫衣` |
| 连帽卫衣、帽衫、hoodie | `assets/color-references/hoodie/` | 3 | `C:\TK-图\连帽卫衣` |
| 短袖、短袖T恤、T恤、short-sleeve | `assets/color-references/short-sleeve/` | 10 | `C:\TK-图\短袖` |

## 参考图文件

### 圆领卫衣

`apricot.png`、`black.png`、`burgundy.png`、`heather-gray.png`、`white.png`

### 连帽卫衣

`black.png`、`gray.png`、`white.png`

### 短袖

`apricot.png`、`black.png`、`blue.png`、`brown.png`、`dark-green.png`、`gray.png`、`heather-gray.png`、`pink.png`、`red.png`、`white.png`

## 读取和匹配

1. 用户指定某个目标类型后，读取该类型目录中的全部图片并按文件名排序。
2. 用户指定颜色时，只选择与该颜色视觉效果相符的参考图；不凭空新增颜色。
3. 对灰色、麻灰、杏色、白色等容易混淆的颜色，直接查看参考图中衣服主体再决定。
4. 未指定颜色时，输出该目标类型的全部颜色版本。
5. 深色和浅色版本都必须保留衣服褶皱、材质和原图图案细节，不能仅做 RGB 平移。
