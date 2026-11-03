# 输出目录与 ZIP 规则

## 默认结构

每张上传图片默认对应一个 `item-XX` 子目录。总目录名称包含目标服装类型：

```text
crewneck-sweatshirt-style-recolor-batch/
|-- item-01/
|   |-- item-01_apricot.png
|   |-- item-01_black.png
|   `-- item-01_white.png
`-- item-02/
    |-- item-02_apricot.png
    |-- item-02_black.png
    `-- item-02_white.png
```

每个 item 目录的最终图片数量等于当前目标类型选定的颜色数量。不要混入原图、遮罩、预览图、`_clean`、`_base` 或其它中间文件。

## 数量计算

最终图片数量：

`输入图片数量 x 当前目标类型选定颜色参考数量`

例如：

- 1 张输入图 x 圆领卫衣 5 个颜色 = 5 张最终图片
- 2 张输入图 x 连帽卫衣 3 个颜色 = 6 张最终图片
- 3 张输入图 x 短袖 10 个颜色 = 30 张最终图片

## 打包

图片生成完成后：

1. 检查每个 `item-XX` 子目录都存在。
2. 检查每个 item 中的图片数量等于选定颜色数量。
3. 检查文件名包含 item 编号和颜色标签。
4. 检查每张图片均为指定目标服装类型，且保留原图的图案、文字、logo、刺绣和装饰。
5. 检查每张最终图片严格小于 `2,000,000` 字节：小于该限制时原样复制；达到或超过该限制时才压缩。
6. 使用 `scripts/package_outputs.py` 复制、校验并生成总目录和同名 ZIP。

例如，圆领卫衣全部 5 色：

```bash
python scripts/package_outputs.py \
  --staging-dir ./staging \
  --output-dir ./deliverables \
  --batch-name crewneck-sweatshirt-style-recolor-batch \
  --expected-images-per-item 5
```

脚本依赖 Pillow：

```bash
python -m pip install Pillow
```

压缩规则：

- 小于 `2,000,000` 字节的图片原样复制，不重新压缩。
- JPG/JPEG/WEBP 超限时优先降低编码质量。
- PNG 超限时优先最大压缩和减少调色板颜色；仍超限时才逐步缩小尺寸。
- 每个 item 目录只允许 PNG、JPG、JPEG 或 WEBP 图片，不能包含其它文件或嵌套目录。
- staging 目录与最终输出目录不能重叠，避免误删输入文件。
