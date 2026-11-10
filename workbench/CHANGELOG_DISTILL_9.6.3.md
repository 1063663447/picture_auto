# 9.6.3 · 沉淀改成「拍板后静默评估」：只把款式通病写进 skill

日期：2026-11-11。后端 + 前端 + 配置改动，**需重启服务**；前端资源 `?v=9.6.8`（Ctrl+F5）。

客户要求（原话）：

> 关于主动沉淀，逻辑应该改为：当对齐的需求拍板后（即用户确定基准图没问题可以改色了以后），
> harness 后台静默评估本次需求对齐的重要性（因为有的问题可能只是针对当前衣服微调，但有的问题
> 是真的因为原 skill 有问题导致衣服款式、图案跑偏）来沉淀自身记忆和 skill，沉淀后每次生图有
> 对应款式时，要在对应款式生图 skill、上下文、记忆中运用，做到尽量降低基准图的返工率。

## 一、评判：一条要求该记多久（`skillbridge/align_judge.py`）

每条对齐要求（对话里每次打回的要求 + 确认备注）都会被判一次：

| 字段 | 含义 |
|---|---|
| `scope` | `style`（这个款式的通病）／`one_off`（只对这一张） |
| `kind` | `garment` 版型构造／`artwork` 图案文字／`colour` 颜色／`material` 面料／`scene` 画面 |
| `importance` | 0~1，越大越该写进款式规则 |
| `promote` | 是否升级成**款式级硬规则**（直接进生图提示词的强制段） |
| `clause` / `negative` | 写进提示词的英文约束 / 否定约束 |
| `reason` | 一句中文依据（写进任务日志） |

两步走，省钱又稳：
1. **启发式**（免费、毫秒级）：命中「袖长/领口/罗纹/印花跑偏/材质画成棉…」→ 款式级；
   命中「这张/这一张/本次/临时…」→ 一次性；「以后/统一/老是/总是」→ 加分。
2. **harness 模型复核**（`defaults.auto_learn_online`，默认开）：让它按同一套口径给 JSON；
   不可用或格式不对就回落启发式。**不会因为评判失败而挡住出图。**

## 二、时机：拍板之后、后台静默

- `approve_master` 先把改色跑起来 → 再 `create_task(distill_review(...))`：
  **接口立即返回**（`distill.state = running`），评估完把结果写进 `job.params.distill` 和任务日志；
- 对话里只补**一行**说明（不追问、不打断）：
  「这次的要求我看过了：1 条升级成这个款式的硬规则（下次生图自动带上），1 条判为这一次的微调，没写进规则。」
- 新增接口 `GET /api/jobs/{job_id}/distill`（前端静默轮询用），`bench.await_distill()` 给测试/收尾用。

## 三、沉淀分层：记忆 + skill

款式文件 `library/prompts/<slug>.json` 里，`lessons` 现在带 `scope / kind / importance / promote / judge / reason`：

| 类型 | 是否注入提示词 | 位置 |
|---|---|---|
| `promote=True` | ✅ | **`SKILL-LEVEL RULES FOR THIS STYLE`**，紧跟款式核心（靠前，模型更守） |
| `scope=style`、`promote=False` | ✅ | `STYLE RULES LEARNED FROM PREVIOUS APPROVED BATCHES`（后面补充） |
| `scope=one_off` | ❌ | 只留档（能看到「这条当时说过」），不进提示词 |

- 新增 `style_promoted`（GarmentType）→ `prompts.promoted_clause()` / `lessons_clause()` 分别出段；
  硬规则最多 4 条、经验最多 6 条，重复只累加命中次数。
- **上下文**：`/api/harness/stream` 的 master 模式会把该款式的
  「硬规则 N 条 / 经验 N 条 / 历史返工 N 轮」写进开场上下文，助手不会重复问、重复记；
  `get_style_skill` 工具也会分层列出来。
- **记忆统计**（看返工率有没有降）：每个款式记
  `stats = {batches, approves, review_rounds, promoted, learned, updated_at}`；
  `GET /api/styles/{slug}/lessons` 会一并返回。

## 四、验证

```powershell
python tools/check_silent_distill.py --show   # 本次新增
python tools/check_auto_learn.py              # 已适配静默评估（等后台结果）
python tools/check_master_gate.py             # 基准闸门全链路
python tools/selftest.py                      # 25 款 / 115 色
```

`check_silent_distill.py` 钉的是客户关心的那四件事：
1. 评判口径：「袖口总是画出罗纹收口」→ style+promote；「这张裁切再往下一点」→ one_off；
2. 拍板前什么都不记；拍板后接口立即返回 `running`，改色照常跑完；
3. 只把款式通病写进提示词：`SKILL-LEVEL RULES` 出现，一次性微调不出现（`裁切` 不在提示词里）；
4. 统计：`review_rounds`、`promoted`、`updated_at` 都记上了。

全部离线脚本 17 个绿；服务已重启。

## 五、已知限制

- 评判用的是文本判断，看不到图：如果运营说的话本身没提款式（只说「重做」），会被判成一次性——这是
  **故意偏保守**（宁可漏记，也不要把一次性要求写成款式硬规则）。
- `promote` 上限 4 条、经验 6 条；要换掉旧的，在前端/接口删掉对应 lesson 即可（`DELETE /api/styles/{slug}/lessons/{id}`）。
- 任务若在评估完成前被删掉，评估结果丢弃（日志里会有一条 warn）。
