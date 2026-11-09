# 9.4.1 改动复核（第三方 AI 的工作）

复核人：接手方（DeepSeek harness）· 复核日期：2026-11-09
复核对象：`CHANGELOG_BLACKGLASS_9.0.0` → `CHANGELOG_ARTWORK_9.4.1`（7 份记录）及其改动的工作树
结论：**方向和实现质量都过关，可以接受**；但有 1 个线上 500、1 个自身回归变红、2 个高危工程项需要处理（见下）。

---

## 0. 一句话结论

| 维度 | 评价 |
|---|---|
| 需求理解 | ✅ 准。界面重做、AI 弹窗、粘贴上传、人物一致性、图案黑块、便携包，都是"能干活"的改动，不是换皮 |
| 完成度 | ✅ 7 个版本串成一条线，每版都有 changelog + 自检脚本 + 验证边界声明（"截图超时所以没做人工视觉验收"这种话说得很实） |
| 与我方既有约定的兼容 | ✅ 效果图双参考 / RESHAPE / fit / 袖口工艺 / 基准派生 / 改图换接口 全部保留，提示词里没有自相矛盾的段落 |
| 线上可用性 | ⚠️ 复核时 `/api/gate` 500（已修）、自有回归 `check_primary_workflow.py` 变红（已修） |
| 工程风险 | ❌ **470MB 产物 + 25 个改动文件全都没提交**（最大风险，见 §4） |

---

## 1. 这一轮到底改了什么（按主题归并）

**A. 界面重做（9.0 → 9.2）**
纯黑 + 深紫毛玻璃（BLACK GLASS）→ 石墨银紫（GRAPHITE），新增 `blackglass/`（5 层 CSS + Three.js 粒子 + effects.js）、
`blackglass-ui.js`、`graphite.css`、`ai-workflow.css/js`。功能上：
- 左右主框同顶同底、款色区与画廊各自滚动、移动端"配置/画布"切换
- 上传改异步小预览（160×200，原文件另存）、前端 20MB 上限
- 成品"闪卡翻面"显影、灯箱 contain、缩略图/预览图/原图三个不同接口（不拿缩略图冒充原图）
- 侧边对话改成居中「AI 创作工作室」，真实 SSE 阶段（不是假进度条），提交后显示真实任务 ID
- 6 处竞态修复：提交互斥、切任务时旧卡片、返工模型乱序、重复 watch、`/api/usage` 显示全员、手填模型被覆盖

**B. 主备接口与计价（9.1.0）**
`default_provider` 改成 `a6api`（"主接口"），65535 降为"备用接口"（label 也改了）；
新增 `providers.a6api.fixed_unit_price = 0.038`，优先级高于模型价目/飞书价目/尺寸档。
UI 只显示"主接口/备用接口"，旧 localStorage 一次性迁移。

**C. 原图/成品双来源 + 模板化返工（9.1.1）**
新增 `sources.py`（按 task/item/view 回溯原始上传图，返工在新任务里留 `originals/` 字节副本）、
`harness_media.py`（原图/成品配对、角色标签、逐文件尺寸判断）。解决"原图是正+反面拼图、成品只剩正面"的丢视角问题。
返工时模型同时拿【当前成品 + 原始上传图】。

**D. 人物一致性（9.3.0 → 9.4.0）**
- 原图有真人 → 锁定人物身份/脸/发/肤色/手/姿态/人数，衣服按款式重绘
- 区分 `subject_choice`（用户选）与 `subject`（批次汇总），混合批次不会被整批覆盖
- 自动返工沿来源链继承主体决策，不再"以已经丢人的成品重新判定"
- 支持定向删人（"只留男模特"），且后续颜色不许把删掉的人加回来
- **取消了我原来的"皮肤缺席=纯衣服"自动硬禁**：`subject.detect()` 现在只在检测到皮肤时返回 `person`，
  其余返回 `auto`（保持原图），只有用户显式选「只有衣服」才上 `NO_PERSON` 硬约束

**E. 图案黑块（9.4.1）**
`PRINT INK VS EXPOSED FABRIC` 条款：区分真实印墨与图案内部/周边露出的布料（腿间、鞋旁、字符孔洞），
禁止把露底布料填成黑墨；原图墨色边界优先于基准图黑块。新任务记 `artwork_policy=ink-and-cloth-v1`。

**F. 便携包（9.3.0/9.4.0/9.4.1）**
`portable/`（start.bat + bootstrap.ps1 + launch.py）+ `releases/PictureWorkbench-9.x-win-x64-internal.zip`
（各 87MB，内置 Python 3.12.10 + 20 个依赖 + Three.js，离线优先，缺文件走国内镜像+sha256）。
单实例锁、`workbench/data` 每台机器独立、修复不覆盖 `.env`/`config.json`。

**规模变化**：后端 9967 → **11458 行**；`app.js` 104 → **121KB**，另有 blackglass-ui.js 22KB、ai-workflow.js 6.7KB；
CSS 从 3 层变 **10 层**；tools 从 50 → **66 py + 5 js**；提示词 6.8KB → **10.9KB/张**。

---

## 2. 我实际验证了什么（不是只看文档）

| 项目 | 结果 |
|---|---|
| 9 项离线交接检查（selftest / effect_ref / no_person / cuff / shape_lock / multi_style / thumb_package / gate / feishu_gate） | **9/9 全绿**（退出码 0） |
| 对方新增的 8 个离线检查 | 7 个通过；`check_primary_workflow.py` **5 个 error（红）** → 已修，见 §3 |
| 缺失脚本核对 | `tools/check_harness_selections.py` 不在 `workbench/tools/`，落在仓库根 `tools/`（能跑，位置不一致） |
| 提示词一致性 | `subject=auto` 无禁人段、"同款各色"派生段在、RESHAPE/fit/效果图角色说明在；**没有** "same garment type, cut"、"full width" 这类与重塑冲突的旧句；段落清单：HEM/SLEEVE/PRINT LOCK/MATERIAL/PHOTOGRAPHIC/ORIGINAL UPLOAD COMPOSITION/PRINT INK VS EXPOSED FABRIC/REFERENCE ROLES/RESHAPE/SUBJECT/KEEP UNCHANGED |
| 我 8.0.1 的修复是否还在 | 改图按所选接口（api.py:1099 `provider=provider or None`）、效果图发送顺序 `[source, effect]`（runner:812-819）都在 |
| 线上界面 | 浏览器实开 8787：新版石墨界面渲染正常，13 款 9 色可选，粘贴上传提示、AI 创作需求框都在 |
| 线上接口 | `/api/health` 200、`/api/skill` 200、`/api/providers` 200、`/api/usage` 200、`/api/preview` 200（garment 模式含 NO PERSON）、**`/api/gate` 500** → 已修，现 200 |
| 计价链路 | `a6api` 生效单价 **0.038**（fixed_unit_price 覆盖），`terra` 走价目表 0.065 |

---

## 3. 复核时发现并已修的两个问题

### 3.1 `/api/gate` 500（线上真 bug，每次开页面都报错）
`service.gate_status()` 原来写死：
```python
gate = self.gate_for(self.get_provider(None))   # 9.1.0 起默认接口是 a6api
status = await gate.status()                    # a6api 标了 unlimited → gate_for 返回 None → AttributeError
```
9.1.0 把默认接口换成 a6api（`unlimited:true`）之后这条路径必然炸，但离线检查都自带 gate，所以没被发现。
影响：界面"队列"胶囊永远拿不到数据，备用接口（65535）的排队信息看不到。
**修法**：默认接口没有闸门时，退到任一"要排队"的接口（现在显示 terra 队列，`in_use 0 / limit 15`），
全都没有就明确返回 `enabled=false`，不再 500。已在 `skillbridge/service.py` 落地并实测 200。

### 3.2 自有回归 `check_primary_workflow.py` 变红（9.1.0 之后没跟着改）
9.4.0 让 `harness_stream` 逐任务读 `source/garment/color/color_hex/item/view`、`ref.file_code`、`ref.rgb`、
以及 `job.id`（`subject.task_kind` 用），而这份 fixture 还是 9.1.0 的 `SimpleNamespace`，缺字段 →
5 个用例直接 AttributeError。**不是产品 bug，是测试过期**，但会让人误以为引擎坏了。
**修法**：把 fixture 补齐成真实 `Task`/`ColorRef`/`Job` 的字段集，现在 **9 个用例 OK**。

---

## 4. ⚠️ 最需要你处理的一件事：470MB 产物和全部改动都没提交

复核时仓库状态：**HEAD 仍停在 `3fc1192 docs: 新增项目交接文档`，对方的 9.0→9.4.1 全部躺在工作树里没提交**——
25 个已修改文件 + 62 个未跟踪条目，其中：

| 路径 | 体积 | 风险 |
|---|---|---|
| `workbench/releases/`（3 个 87MB 便携包 + 校验文件） | **262 MB** | GitHub 单文件 100MB 硬限，仓库会爆 |
| `workbench/ui-sandbox/live-3d-v1/`（含一份完整 workbench 副本 + data/） | **207 MB** | 同上，且含客户数据 |
| `workbench/portable/`、`validation/`、7 份 CHANGELOG | 小 | 应该提交 |

我已把这三类加进 `workbench/.gitignore`（`releases/`、`ui-sandbox/live-3d-v1/`、`validation/`），
现在 `git add -A` 不会再吃掉 470MB。**但代码本身仍未提交** —— 这是最大的丢失风险（换机/误删/接下来谁再改一版就混在一起了）。

建议（我可以直接执行）：
1. 一次提交对方的全部代码改动 + 7 份 CHANGELOG + 新增模块 + 便携包构建脚本（不含 ZIP）
2. 提交信息把 9.0.0→9.4.1 七轮要点压缩成一条，或按版本拆 7 个 commit（要拆就得逐个文件分组，我可以做）
3. push（网络会抖，重试 3~5 次）

---

## 5. 需要你拍板/留意的点

1. **`fixed_unit_price = 0.038` 是"报价"还是"成本"？**
   后台真实消费记录是 **¥0.005978/张**（8.0.1 时按这个记）。现在用量账本、配额、界面"本次花费"都按 0.038，
   等于**把成本放大 6.4 倍**。如果 0.038 是给客户/内部的报价，建议拆成 `quote_price` 与 `cost_price` 两个字段，
   否则"今天花了多少钱"这个数字会持续失真（当前账本 total 109 张 → ¥1.2599，混合了旧价与新价）。
2. **"纯衣服禁人"从"自动"退了半步**：`skin≈0` 现在返回 `auto`（中性提示词，不硬禁），只有显式选「只有衣服」才硬禁。
   理由充分（背面/戴帽/手脸裁切的真人图会被误判成空衣服，导致 9.4.0 §3 的"人物头腿丢失"）。
   代价是最初那个"平铺图忽然冒出模特"的问题**只在 auto 的中性话术下被弱约束**。建议：给常用平铺批次默认选「只有衣服」。
3. **提示词涨到 10.9KB/张**（原来 6.8KB），条款从 6 段变 11 段。每段都有实测依据，但边际收益在下降、注意力会稀释；
   建议下次先做 A/B（同图同色，加/不加新段各一张），别再一路加条款。
4. **CSS 10 层同屏加载**（style → aurora → blackglass/5 层 → blackglass.css → graphite.css → ai-workflow.css）。
   旧主题 `aurora.css`/`glass.css` 仍在前，叠加后谁赢靠加载顺序，后续改样式容易互相踩；建议收敛到"一层 tokens + 一层组件"。
5. `tools/check_harness_selections.py` 在仓库根 `tools/`，和其它检查脚本不在同一处，建议挪进 `workbench/tools/`。
6. 便携包只在一台机器上验证过（对方也写明了"没有全新虚拟机实测"），发给同事前建议在一台干净 Windows 上试一次。

---

## 6. 复核结论与建议动作

**接受本轮改动**：功能方向对、边界诚实、关键业务契约（效果图/版型/图案/命名/打包/队列）没有被我方后续修改破坏。

建议顺序：
1. 我来提交（§4），先保证不丢
2. 决定 `0.038` 的语义（§5.1）——这条会影响所有"花了多少钱"的判断
3. 让平铺批量默认「只有衣服」（§5.2），或把 auto 的禁人话术再硬一档
4. 抽一次真实的「效果图版型 + 图案黑块 + 人物保留」三项联合小样（各 1 张，约 ¥0.02），确认 8.x 与 9.4.1 叠加后仍对
