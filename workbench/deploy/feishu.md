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
| 链接分享 | **组织内获得链接的人可编辑**（`tenant_editable`，用 `tools/feishu_share_editable.py` 设置/复查） |
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
| 角色 | `admin` = 管理员（能进管理后台 / 看全站用量）；其他值 = 普通成员（只能做图） |
| 状态 | `启用` / `停用`（停用后立即无法登录） |
| 额度元 | **billing 上限**：成员发起任务时，已用 + 本次预估 超过额度就直接拦下；填 0 = 不限（管理员不受限） |
| 创建时间 / 备注 | 备注 |

- 当前管理员：**admin / Asd123456.**（密码可用 `tools/feishu_account_admin.py passwd admin 新密码` 或管理后台「改密码」重置）。
- 登录入口：打开站点 → 自动跳 `/login`；会话 Cookie `wb_session` 有效期 12 小时（`config.json → server.session_ttl`）。
- `?token=<auth_token>` 仍然有效（机器/应急访问，等同管理员），不受账号表影响。
- 飞书表读不到时会退回 `data/accounts.json` 本地兜底账号，避免把自己锁在门外。

## 管理后台（/admin）

只对 `角色 = admin` 开放（非管理员访问会被弹回工作台并提示）：

| 区域 | 内容 |
|---|---|
| 顶部 KPI | 总图片 / 总花费 / 账号数 / 账号池额度（总额度 + 已用）/ 今日 |
| 新建子账号 | 账号 + 初始密码 + 显示名 + 角色 + 额度元 + 备注（建好即可登录） |
| 账号管理 | 每个账号的角色、启用/停用、额度元（就地改）、已用、剩余额度进度条、张数、次数；操作：设为/降为管理员、停用、改密码、删除 |
| 用量排行 | 按账号的张数条形图 |
| 模型用量 | 按模型的张数条形图 |
| 近 14 天 | 每天张数柱状图 |
| 最近记录 | 最新 20 条：时间 / 账号 / 成品 / 模型 / 张数 / 金额 / 任务ID |

- 数据来源：账号与价格来自飞书表，**用量优先从飞书记录聚合**（`usage_source: feishu`），飞书读不到才用本地 `data/usage.json`。
- 接口（都要管理员）：`GET /api/admin/overview`、`POST /api/accounts`、`PATCH /api/accounts/{账号}`、`POST /api/accounts/{账号}/password`、`DELETE /api/accounts/{账号}`（不能删除/降级自己）。
- 额度拦截在**建任务时**执行（`POST /api/jobs`、`/api/jobs/{id}/rework`、harness 里的 `submit_generation` 同样生效），超限返回 400 并说明差多少。
- 工作台右上角有 **管理后台** 入口（仅管理员可见），也可直接访问 `/admin`。

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
python tools/check_account_e2e.py --sync             # mock 跑一单，验证「账号」写进记录
python tools/feishu_account_admin.py list            # 列账号（角色/状态/有无哈希）
python tools/feishu_account_admin.py add zhang "Pass@2026" --display 张三 --role member
python tools/feishu_account_admin.py passwd zhang "新密码"
python tools/feishu_account_admin.py del zhang
python tools/cleanup_feishu_jobs.py <任务ID>          # 删掉某个任务在飞书里的记录（清理测试）
python tools/fix_record_table_fields.py              # 万一主字段被改名，改回「成品」
python tools/feishu_share_editable.py --check        # 看表格链接分享权限（可编辑 / 可阅读）
python tools/feishu_share_editable.py                # 改成「组织内获得链接的人可编辑」
python tools/check_admin_http.py                     # 登录 + 管理后台 + 账号 CRUD + 额度拦截
python tools/check_web_static.py                     # 页面/脚本的 id 与图标一致性
```

任务跑完会**自动写**，日志里出现：`已写入飞书多维表格 N 行（含图片附件）`，
`job.params.feishu_rows` / `feishu_table` 也会记录。

## 用量：谁做了什么

- 记录表每行的「账号」= 发起人；工作台 `data/usage.json` 也按账号记账（`job_id` 幂等，不重复计费）。
- **账号用量优先从飞书记录表聚合**（`张数`/`金额元`/`任务ID`/`成品`/`时间`），Render 这类没有持久盘的部署重启后数字也不会丢；飞书读不到时才回退本地账本。
- 管理员界面右上角 **账号用量** → 每个账号的 张数 / 花费 / 调用次数 / 最近三次成品（来自 `/api/accounts`，非管理员 403）。
- 汇总接口：`GET /api/usage`（今日、累计、按账号 `accounts[]`）、`GET /api/accounts`（管理员，`usage_source: feishu|ledger`）。

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
