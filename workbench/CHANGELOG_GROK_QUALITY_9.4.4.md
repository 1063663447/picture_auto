# 9.4.4 · grok 系列 quality 收敛（不再发 high 被上游打回）

日期：2026-11-09。后端改动，需重启服务；前端资源版本不变。

## 问题

备用接口（65535）用 grok 生图时报：

```
upstream_error: upstream rejected the request:
This model only supports the following quality value(s): low, medium, auto.
```

工作台默认 `quality=high`（gpt-image 系列的口径），grok 只认 `low / medium / auto`，
于是请求被上游直接打回 —— 白等一次，还可能已经计费。

## 修法（两道）

1. **建单时收敛**：`config.json → defaults.quality_rules` 按模型前缀声明允许值：

   ```jsonc
   "quality_rules": [
     { "match": "grok-imagine",
       "allowed": ["low", "medium", "auto"],
       "default": "auto",
       "note": "grok 生图只接受 low/medium/auto；传 high 会被上游打回" }
   ]
   ```

   `Provider.quality_for(model, quality)` 在建单时把 `high` 换成 `auto`，
   所以**任务参数、界面显示、预估**三处一致（不会出现「界面 high、实际 auto」）。
   规则可按 provider 覆盖（`providers.<name>.quality_rules`，写在 provider 里的优先）。

2. **发请求前再兜一次**：`openai_compat._form()` 写 `quality` 前也过一遍 `quality_for()`。
   这样**历史任务重试、返工**（params 里存的还是 high）也不会再被打回。
   收敛成 `auto` 时该字段**直接不发送**（`auto` 是上游默认值），不会多传一个参数。

## 验证

- 离线 `python tools/check_model_params.py`：
  - `grok-imagine-image / -image-2.0 / -image-quality / -video`：`high → auto`、
    `medium/low` 保留、空值 → `auto`
  - `gpt-image-2.5-/2.6-sunburst` 和主接口 a6api 的 `high` **不受影响**
  - 真正发出的 multipart 表单：grok + high → **不带 quality 字段**；grok + medium → `medium`；
    gpt-image + high → `high`
  - 建单后任务参数记录为 `auto`
- 真机 1 张（备用接口 / `grok-imagine-image-2.0` / 请求时故意给 `quality=high`）：
  任务 `20261109-180834-f5e7`，**done，35.9s**，1024×1024，产出干净的黑色短袖 T
  → `data/test/grok-quality/`（报价口径约 ¥0.1）
- 10 个离线脚本全绿：`selftest / check_garment_lock / check_model_params / check_effect_ref /
  check_no_person / check_cuff / check_shape_lock / check_primary_workflow /
  check_garment_priority / check_person_consistency`

## 顺带说明

- grok 返回的是 **JPEG** 字节；任务按 `output_format` 处理，若选「自动」就保留原格式（`.jpg`），
  选 `png` 才会转码。这次测试我手动存成 `.png` 名字，实际内容仍是 JPEG —— 不是 bug。
- 以后遇到别的模型也有参数限制（比如某个模型不收 `input_fidelity`、`seed`），
  照这个 `quality_rules` 的样子在 config 里加一条即可，不用改代码。
