# 用飞书多维表格当记录库（替代 R2）

每完成一个任务，工作台会把「做了什么 / 什么模型 / 多少张 / 多少钱 / 成品在哪」写进一张多维表格，
运营直接在飞书里筛选、求和、对账。**不需要对象存储**；R2 相关代码已摘除。

## 一、5 分钟配置（一次性）

1. **建企业自建应用**
   https://open.feishu.cn/app → 创建企业自建应用 → 记下 **App ID / App Secret**（「凭证与基础信息」页）
2. **加权限**
   应用 → 权限管理 → 搜索并添加：
   - `bitable:app`（查看、编辑、删除多维表格）—— **必需**
   - 如需把 ZIP 当附件写进表格：再加 `drive:drive`
3. **发布**
   应用 → 版本管理与发布 → 创建版本 → 申请发布（企业内自建应用，管理员通过即可）
4. **建多维表格**
   飞书里新建一个「多维表格」，打开后从地址栏取两段：
   ```
   https://xxx.feishu.cn/base/<APP_TOKEN>?table=<TABLE_ID>&view=vew...
                              ^^^^^^^^^^         ^^^^^^^^^
   ```
5. **把应用加成协作者**（最容易漏的一步）
   多维表格右上角「···」→ 协作者 → 添加「应用」→ 选你刚建的应用 → 权限 **可编辑**
6. **填配置**
   `.env`：
   ```ini
   FEISHU_APP_ID=cli_xxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
   ```
   `config.json → feishu`：
   ```json
   "feishu": {
     "enabled": true,
     "app_id_env": "FEISHU_APP_ID",
     "app_secret_env": "FEISHU_APP_SECRET",
     "app_token": "bascnxxxxxxxxxxxx",
     "table_id": "tblxxxxxxxx",
     "per": "task",
     "upload_zip": false
   }
   ```

## 二、体检 + 自动建字段

```powershell
python -m skillbridge.feishu --check         # 鉴权 + 读现有字段
python -m skillbridge.feishu --init-fields   # 自动补齐缺的字段（不动已有字段）
python -m skillbridge.feishu --sync-job 20261103-150000-abcd   # 补写某个历史任务
```

自动创建的 14 个字段（分类字段用"文本"，避免单选选项要人工维护）：

| 字段 | 类型 | 内容 |
|---|---|---|
| 成品 | 文本 | `item-01 / 黑色` |
| 任务ID | 文本 | 便于聚合与追溯 |
| 完成时间 | 日期 | 毫秒时间戳，可在飞书按日筛选 |
| 款式 / 颜色 / 模型 / 尺寸 / 接口 / 状态 | 文本 | 生成参数与结果 |
| 张数 | 数字 | 成功的记 1、失败记 0 |
| 单价元 / 金额元 | 数字 | 按 `pricing.json`（人民币），可直接求和 |
| 本地ZIP | 文本 | 相对路径（仅本机部署有意义） |
| 备注 | 文本 | 失败原因 / 本次特殊要求 |

## 三、两种粒度

- `per: "task"`（默认）**一行一个成品** —— 适合按颜色/模型做透视，失败也留痕
- `per: "job"` **一行一个任务** —— 行数少，适合只看批次

## 四、附件（可选，默认关闭）

`upload_zip: true` 时，任务完成后把 ZIP 传到多维表格附件字段（需要 `drive:drive` 权限）。
限制：单文件 ≤ 20MB、走 Fly Drive 上传会额外消耗频次；成品多时建议只传汇总 ZIP。

## 五、和部署的关系

- **本地 / 隧道部署**：`data/` 在本地，飞书表格是索引，ZIP 直接下载。
- **Render 免费实例**：磁盘是临时的，重启会清 `data/` —— 此时**飞书表格是唯一持久记录**，
  建议 `upload_zip: true` 或出图后立刻下载 ZIP；表格里的「本地ZIP」字段在重启后会失效，仅作参考。
- 写入失败不会影响出图：只记一条 warn 日志（"飞书写入失败：..."），任务照常完成。
