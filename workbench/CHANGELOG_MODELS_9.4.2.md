# 9.4.2 · 备用接口新增 gpt-image-2.6-flare / gpt-image-2.6-sunburst（按次 0.038）

日期：2026-11-09。只动配置与价目，不改前端资源版本（沿用 9.4.1），刷新页面即可看到新模型。

## 改了什么

| 文件 | 改动 |
|---|---|
| `pricing.json` | `models` 新增 `gpt-image-2.6-flare: 0.038`、`gpt-image-2.6-sunburst: 0.038`（按次）；`-eco` / `-native` 四个变体记 `null`（无报价，界面显示「未定价」）；`updated` 与 `note` 补一行说明 |
| `config.json` | `providers.terra.model_prices` 增加同两条 0.038（接口级覆盖，优先级高于 pricing.json 与飞书价目）；`terra.note` 写明这两个模型要走闸门排队 |
| `README.md` | 接口表改成「主接口 a6api / 备用接口 terra」，新增一节说明这次加了什么、为什么两处都要写 |

## 为什么不是只改 pricing.json

价格解析顺序是 **接口覆盖 → 飞书「模型定价」表 → pricing.json**。
飞书那张表存在时会把本地价目整份替换掉，而表里目前只有 7 行（没有 2.6），
所以只改 `pricing.json` 会在飞书刷新后变回「未定价」。接口级 `model_prices` 是唯一稳定的那一层。

两边都写了以后实测（`Workbench.list_models('terra', refresh=True)`）：

```
模型总数: 36 | 图片模型: 27
  gpt-image-2.6-flare              ¥0.038/张
  gpt-image-2.6-sunburst           ¥0.038/张
  gpt-image-2.6-flare-eco          未定价
  gpt-image-2.6-sunburst-eco       未定价
  gpt-image-2.6-flare-native       未定价
  gpt-image-2.6-sunburst-native    未定价
```

飞书表刷新后（`source=feishu`）这两个仍然是 ¥0.038，证明覆盖生效。

## 真机验证（各 1 张，合计报价 ¥0.076）

任务都走备用接口的飞书闸门，模型 `gpt-image-2.6-sunburst` / `gpt-image-2.6-flare`：

| 模型 | 任务 | 结果 | 耗时 | 成品 |
|---|---|---|---|---|
| `gpt-image-2.6-sunburst` | `20261109-173759-9125` | done 1/1，ZIP 正常 | ~40s | 1024×1024，1,675,718 B |
| `gpt-image-2.6-flare` | `20261109-173859-4c53` | done 1/1（打包阶段被测试脚本中断，图片已另存） | ~45s | 1024×1024，1,646,148 B |

原图：客户那张带 06 印花的红 T；款式 10 弹力短袖 · 深酒红；画面主体「只有衣服」。
成品见 `data/test/model-26/2.6-sunburst.png`、`2.6-flare.png`：版型贴合效果图（紧身短袖）、
06 印花与两侧袖标都在、无人物。`2.6-flare` 对袖标上 "BROOKLYN" 小字的保留略好。

## 需要注意

1. **飞书定价表里 `gpt-image-2.6-sunburst` 已有人工填的 0.08**。现在接口覆盖 0.038 优先，所以界面和账本按 0.038。
   如果 0.08 才是对外报价，请二选一改掉（改飞书表那行，或删掉 config 里的覆盖），否则两个口径会打架。
2. 这次测试还出现过一条 `20261109-173724-44a5`（服务重启导致中断），是我的重启打断了它，不是模型问题；已清理该测试任务。
3. 报价口径：`¥0.038/张` 是**报价**（运营给的数），不是网关后台成本价（成本口径另算，见主接口那边 0.005978 的说明）。
