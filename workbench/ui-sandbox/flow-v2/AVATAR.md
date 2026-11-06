# 头像生成状态

用户请求：生成一张符合星雾工作台风格、适合左下角圆形裁切的用户头像。

已调用 PI-Desktop 的 `pi-desktop/imagegen` 技能与 `GenerateImages` 工具，数量 1。工具返回：

```text
IMAGE_NOT_CONFIGURED
Configure an image generation model in Settings > Models > Image generation model before generating images. Do not substitute another model.
```

目前没有生成成功的图片，没有自动重试，也没有改用项目正式生图接口。请在“设置 → Models → Image generation model”配置后继续这一步。

界面当前使用 `avatar-placeholder.svg`：本地矢量头像占位，非 AI 生成结果。头像菜单、状态点、焦点和展开反馈已经实现；生成成功后可直接替换三个头像位置的图片引用。

按最新紫黑方向调整后的待用提示词（尚未再次调用）：

> Create exactly one premium user avatar for a luxurious purple-black fashion image-generation workbench. Square 1:1 image, centered head-and-shoulders portrait with generous circular-crop-safe margins. An original stylized androgynous East Asian creative director, short sculpted silver-violet hair, warm ivory skin, elegant black high-collar clothing. Dark amethyst edge light, restrained silver highlights, very dark violet-black background. Editorial fashion illustration, silky matte materials, clean confident shapes, calm and polished, a readable face at 32px. Clear tonal separation without a bright background. No teal, no cyan, no mint lighting, no rainbow gradients. No text, letters, logos, watermark, frame or multiple portraits.
