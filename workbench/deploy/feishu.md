# 飞书多维表格：记录库 + 模型定价 + 账号（数据库全在飞书）

三张表，一套应用，一个 `app_token`：

| 表 | `table_id` | 作用 | 谁来改 |
|---|---|---|---|
| 成品记录 | `tblvrb2DVxO7s28c` | 每个成品一行 + 成品图/参考图附件 | 系统自动写，运营只读（可改备注） |
| 模型定价 | `tblORNn4X28tuc0k` | 21 个图片模型的单价与分档价 | **人工可改**，工作台 60 秒内生效 |
| 账号 | `tbl9dfa7LVmtKgp4` | 登录账号、角色、状态、额度 | **人工可改**（新增/停用/改密码） |

每个成品写一条记录，**成品图 / 参考图**直接用附件字段挂在记录上（不需要 zip，也不依赖本机磁盘）。
时间、账号、款式、颜色、模型、尺寸、张数、单价、金额、接口、状态一并记录，运营在飞书里能直接筛选、求和、对账。

## 当前已建好的表

| 项 | 值 |
|---|---|
| 表格链接 | https://ocnbd3tr10x9.feishu.cn/base/Cg19bjnLJaAU7CsYPB5cO3qynrb |
| `app_token` | `Cg19bjnLJaAU7CsYPB5cO3qynrb` |
| 记录表 `table_id` | `tblvrb2DVxO7s28c` |
| 定价表 `price_table_id` | `tblORNn4X28tuc0k` |
| 账号表 `account_table_id` | `tbl9dfa7LVmtKgp4` |
| 应用 | `cli_aa633e549a79dcf4`（凭据在 `.env`） |
| 权限 | `bitable:app` + `drive:drive`（附件上传已实测通过） |

> 表是「应用」创建的，你在飞书里若看不到它：打开上面的链接，右上角「···」→ 协作者 → 把自己加成「可管理」。
> 也可以把邮箱给我，我用接口直接把你加成协作者。

## 账号与登录

| 字段 | 说明 |
|---|---|
| 账号 | 登录名（唯一，不区分大小写） |
| 显示名 | 界面右上角显示的名字 |
| 密码哈希 | `pbkdf2_sha256$迭代$盐$哈希`（工作台登录成功后自动写入） |
| 明文密码 | 运营图省事可以直接往这里写密码，**首次登录成功后会被自动升级成哈希并清空** |
| 角色 | `admin` = 管理员（能看账号用量）；其他值 = 普通成员 |
| 状态 | `启用` / `停用`（停用后立即无法登录） |
| 额度元 | 记账用，界面展示 |
| 创建时间 / 备注 | 备注 |

- 初始账号 **admin / Apparel@2026**（已建好，进去后请在飞书表里把「明文密码」改成新密码，或直接改「密码哈希」列旁的路数：把新密码写进「明文密码」，登录一次即自动哈希）。
- 登录入口：打开站点 → 自动跳 `/login`；会话 Cookie `wb_session` 有效期 12 小时（`config.json → server.session_ttl`）。
- `?token=<auth_token>` 仍然有效（机器/应急访问），不受账号表影响。
- 飞书表读不到时会退回 `data/accounts.json` 本地兜底账号，避免把自己锁在门外。

## 定价在飞书里改

工作台启动与每次 `/api/pricing` 时读一次「模型定价」（60 秒缓存），**以表为准**，读不到才回退 `pricing.json`：

| 字段 | 类型 | 说明 |
|---|---|---|
| 模型 | 文本 | 网关模型 ID，如 `gpt-image-2-auto` |
| 单价元 | 数字 | 按次单价；分档模型留空 |
| 1K档 / 2K档 / 4K档 | 数字 | 分档单价（≤1.6MP→1K、≤4.2MP→2K、否则 4K） |
| 其他档 | 数字 | 尺寸无法解析时的兜底价 |
| 启用 | 文本 | 填 `否` / `false` / `禁用` 即不计价（界面显示未定价） |
| 备注 | 文本 | 价格来源说明 |

改完价 → 界面刷新（或等 60 秒）→ 模型下拉价签与「预计花费」立刻跟着变。单价留空 = 未定价，**绝不猜价**。

## 字段结构（记录表 17 个，`deploy/setup_feishu_tables.py` 可幂等重建）

| 字段 | 类型 | 说明 |
|---|---|---|
| 成品 | 文本（主字段） | `item-01 / 黑色` |
| 任务ID | 文本 | 同一批任务聚合用 |
| 时间 | 日期 | 记录完成时刻（毫秒时间戳），可按日筛选 |
| **账号** | 文本 | 谁发起的任务（用量按此聚合） |
| 款式 / 颜色 / 模型 / 尺寸 / 接口 / 状态 | 文本 | 生成参数与结果 |
| 张数 | 数字 | 成功记 1、失败记 0 |
| 单价元 / 金额元 | 数字 | 按 `pricing.json`（人民币），可直接求和 |
| 备注 | 文本 | 失败原因 / 本次特殊要求 / 附件异常 |
| **成品图** | 附件 | 该记录对应的成品图（PNG/JPG 头像级预览） |
| **参考图** | 附件 | 该颜色所用的内置色卡（用来核对颜色） |
| **原图** | 附件 | 可选，默认不上传（`upload_source: true` 打开） |

## 配置（`config.json → feishu`）

```json
"feishu": {
  "enabled": true,
  "app_id_env": "FEISHU_APP_ID",
  "app_secret_env": "FEISHU_APP_SECRET",
  "app_token": "Cg19bjnLJaAU7CsYPB5cO3qynrb",
  "table_id": "tblvrb2DVxO7s28c",
  "price_table_id": "tblORNn4X28tuc0k",
  "account_table_id": "tbl9dfa7LVmtKgp4",
  "per": "task",            // task=一行一个成品（默认，失败也留痕）；job=一行一任务
  "upload_images": true,    // 上传 成品图 + 参考图
  "upload_source": false    // 是否也上传原图
}
```

`.env`：
```ini
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

## 命令行

```powershell
python -m skillbridge.feishu --check                 # 鉴权 + 读字段
python -m skillbridge.feishu --init-fields           # 补齐缺的字段（不动已有）
python -m skillbridge.feishu --sync-job <任务ID>      # 补写某个历史任务（含图片）
python deploy/setup_feishu_tables.py --app-id ... --app-secret ... \
       --app-token ... --record-table-id ... \
       --admin admin --admin-password "Apparel@2026"  # 建/整理 记录表+定价表+账号表（幂等）
python tools/check_accounts.py                       # 账号/密码/定价表 连通性自检
python tools/check_login_http.py                     # 登录、会话、账号用量 HTTP 自检
```

任务跑完会**自动写**，日志里出现：`已写入飞书多维表格 N 行（含图片附件）`，
`job.params.feishu_rows` / `feishu_table` 也会记录。

## 用量：谁做了什么

- 记录表每行的「账号」= 发起人；工作台 `data/usage.json` 也按账号记账（`job_id` 幂等，不重复计费）。
- 管理员界面右上角 **账号用量** → 每个账号的 张数 / 花费 / 调用次数 / 最近三次成品（来自 `/api/accounts`，非管理员 403）。
- 汇总接口：`GET /api/usage`（今日、累计、按账号 `accounts[]`）。

## 从零配一遍（换租户或重装时）

1. 建企业自建应用 https://open.feishu.cn/app → 拿 App ID / App Secret
2. 权限：`bitable:app`（必需）、`drive:drive`（附件必需）→ 创建版本并发布
3. 建一个多维表格，从地址栏取 `app_token`（`/base/` 后）与 `table_id`（`?table=` 后）
4. 把应用加为该多维表格的协作者（可编辑）
5. `python deploy/setup_feishu_tables.py ...` 一次建好三张表并写初始定价/管理员
6. 把三个 `table_id` 填进 `config.json → feishu`，`python -m skillbridge.feishu --init-fields`

## 历史回拉：从飞书批量打包

不需要服务器本地文件，直接以飞书为准：

```powershell
# 列表（可按 job_id / color 过滤）
curl "http://127.0.0.1:8787/api/feishu/records?limit=200&token=<auth_token>"
# 打包（record_ids 省略则按过滤条件全导）
curl -X POST "http://127.0.0.1:8787/api/feishu/export?token=<auth_token>" `
     -H "Content-Type: application/json" -d "{""record_ids"":[""recxxx""]}" -o history.zip
```

界面入口：「历史任务」卡片右上角 **飞书历史 · 打包下载** → 勾选（默认只列有成品图的）→ 打包下载。
ZIP 内按「成品」字段命名，如 `feishu-history/item-01-黑色.png`；下载失败的记录会写进 `_missing.txt`。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `code 9499 Bad Request` 列记录失败 | 飞书的 `GET /records` 对新建表偶尔报这个；改用 `POST /records/search`（代码里已是 search） |
| 附件上传失败 `code 1061xxx` | 没开 `drive:drive` 权限，或应用不是该表协作者 |
| 只写了行、没有图片 | `upload_images` 关着；或该颜色没有对应色卡；或任务是从 job.json 反序列化的老任务（`source` 字段需要新版才会保存） |
| 表格里看不到数据 | 表是应用创建的，用「··· → 协作者」把自己加进去 |
| 登录一直失败 | 账号表里「状态」不是启用；或密码写错（改「明文密码」→ 重新登录一次即自动转哈希） |
| 改了价格界面没变 | 表里「启用」列填了「否」；或还在 60 秒缓存内；或该模型「单价元」与分档价都空着（= 未定价） |
| 账号用量为空 | 老任务没有「账号」字段（默认记成 anonymous，会以「历史账号」出现在列表里） |
