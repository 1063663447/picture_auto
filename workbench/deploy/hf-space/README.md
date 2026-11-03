---
title: Apparel Recolor Workbench
emoji: 👕
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# 服装改款换色工作台（Docker Space）

这个 Space 只是把 `workbench` 的镜像跑起来。真实配置在 `workbench/config.json` 与 Space 的
**Settings → Variables and secrets** 里：

| 名称 | 类型 | 说明 |
|---|---|---|
| `TERRA_API_KEY` | secret | 65535 网关 Key（图片接口走 sub-proxy-us） |
| `DEEPSEEK_API_KEY` | secret | harness 用的对话模型 |
| `WORKBENCH_DATA_DIR` | variable | 可选，例如 `/data`（Space 磁盘仍会随重启清空） |
| `PORT` | — | 平台注入，Dockerfile 已处理 |

访问：`https://<用户名>-<space名>.hf.space/?token=<config.json 里的 auth_token>`

注意：
- Space 免费 CPU 实例在闲置后会休眠，冷启动约 30~60s；
- 磁盘是临时的，Space 重启/重建后 `data/` 下历史任务会丢（成品记得及时下载 ZIP）；
- 生成任务跑在 Space 里，单张 30~120s 属于正常长请求，已用 SSE 推进度。
