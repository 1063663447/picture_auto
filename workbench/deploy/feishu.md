# 飞书多维表格记录库（含图片附件）

每个成品写一条记录，**成品图 / 参考图**直接用附件字段挂在记录上（不需要 zip，也不依赖本机磁盘）。
时间、款式、颜色、模型、尺寸、张数、单价、金额、接口、状态一并记录，运营在飞书里能直接筛选、求和、对账。

## 当前已建好的表

| 项 | 值 |
|---|---|
| 表格链接 | https://ocnbd3tr10x9.feishu.cn/base/Cg19bjnLJaAU7CsYPB5cO3qynrb |
| `app_token` | `Cg19bjnLJaAU7CsYPB5cO3qynrb` |
| `table_id` | `tblvrb2DVxO7s28c` |
| 应用 | `cli_aa633e549a79dcf4`（凭据在 `.env`） |
| 权限 | `bitable:app` + `drive:drive`（附件上传已实测通过） |

> 表是「应用」创建的，你在飞书里若看不到它：打开上面的链接，右上角「···」→ 协作者 → 把自己加成「可管理」。
> 也可以把邮箱给我，我用接口直接把你加成协作者。

## 字段结构（16 个，`deploy/setup_feishu_table.py` 可幂等重建）

| 字段 | 类型 | 说明 |
|---|---|---|
| 成品 | 文本（主字段） | `item-01 / 黑色` |
| 任务ID | 文本 | 同一批任务聚合用 |
| 时间 | 日期 | 记录完成时刻（毫秒时间戳），可按日筛选 |
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
python deploy/setup_feishu_table.py --app-id ... --app-secret ... \
       --app-token ... --table-id ...                # 重建/整理表结构（幂等）
```

任务跑完会**自动写**，日志里出现：`已写入飞书多维表格 N 行（含图片附件）`，
`job.params.feishu_rows` / `feishu_table` 也会记录。

## 从零配一遍（换租户或重装时）

1. 建企业自建应用 https://open.feishu.cn/app → 拿 App ID / App Secret
2. 权限：`bitable:app`（必需）、`drive:drive`（附件必需）→ 创建版本并发布
3. 建一个多维表格，从地址栏取 `app_token`（`/base/` 后）与 `table_id`（`?table=` 后）
4. 把应用加为该多维表格的协作者（可编辑）
5. 填 `.env` 与 `config.json` → `python -m skillbridge.feishu --init-fields`

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
