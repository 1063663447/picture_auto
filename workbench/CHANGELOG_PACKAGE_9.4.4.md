# 9.4.4 · 重新打包便携版（发给同事用，解压即用、无需配 API）

日期：2026-11-09。产物：`releases/PictureWorkbench-9.4.4-win-x64-internal.zip`。

## 打包做了什么

沿用上一版（9.4.1）同一套便携机制，只是把**当前代码与配置**重新打进去：

| 组成 | 内容 |
|---|---|
| 内置运行时 | Python 3.12.10 embed + 20 个固定版本 wheel（fastapi / uvicorn / httpx / Pillow / python-multipart 及其依赖）+ 微软运行库 DLL |
| 应用 | `workbench/`（serve.py、cli.py、skillbridge 全部模块、web 全部前端、library 10 款式 49 效果图、pricing.json、config.json、`.env`）+ `apparel-style-recolor/`（SKILL.md 与参考图） |
| 内部配置 | `.env` 已带 TERRA_API_KEY / A6API_API_KEY / DEEPSEEK_API_KEY / FEISHU_APP_ID / FEISHU_APP_SECRET；`config.json` 带主备接口、2.6 双模型报价、grok quality 规则、models_only_priced、飞书表 id |
| 离线自愈 | `offline-cache/`：python zip + 20 wheel + `application.zip`（应用整包）+ `requirements.lock` + `package-manifest.json`（每个文件的 sha256） |
| 启动器 | 根目录 `一键启动.bat` / `一键检查修复.bat` / `使用说明.txt` / `bootstrap.ps1` / `start.bat` / `package-info.json` |
| 不含 | 历史任务（`workbench/data`）、本机端口/访问令牌（各机器首启动自动生成） |

构建资产是从上一版包里直接提取的（`tools/extract_portable_assets.py`：embed python + 20 wheel + 镜像 URL 缓存），
所以这次重建**不需要联网找 wheel**；`tools/build_internal_portable.py` 的 VERSION 改为 9.4.4，
并把 9.4.2~9.4.4 的 changelog、HANDOVER、REVIEW 一起打进包。

## 产物

```
releases/PictureWorkbench-9.4.4-win-x64-internal.zip        87.3 MB
releases/PictureWorkbench-9.4.4-win-x64-internal.zip.sha256 bb64d16c5ee8714e6eb56549510e5336058c4e2cff5f5b8d7199cd32fe70fe19
releases/PictureWorkbench-9.4.4-verification.json
releases/发给同事_9.4.4.txt                                （可直接复制到群里的说明）
```

解压后 125.6 MB / 1403 个条目：runtime 1178、workbench 167、skill 23、offline-cache 23。

## 验证（三个层次）

1. **结构自检** `tools/verify_portable_9_4_4.py`：必需文件齐全、20 个 wheel、50 张效果图、
   无 `workbench/data`、无 pyc；`config.json` 里 2.6 双模型报价 / `models_only_priced` / grok quality 规则都在；
   `prompts.py` 含 `GARMENT TYPE LOCK`、`service.py` 含模型过滤；`.env` 五个密钥都在（只校验长度不打印）；清单 193 条应用文件 + 1143 条依赖文件。
2. **解压即跑** `tools/check_portable_run_9_4_4.py`：干净目录解压 → 用**包内** `runtime/python.exe` 起服务 →
   `/api/health` ok（13 款 / 9 接口）、首页 200、`/api/skill` 10 个业务款式、备用接口模型 9 个（隐藏 27）、
   价目来源 feishu 7 条 → 跑一单 **mock** 生图（不花钱）done、成品图 650KB → 关服务。
3. **同事的真实路径**：直接跑包里的 `bootstrap.ps1 -NoBrowser`（等同于双击「一键启动.bat」，只是不开浏览器）：
   六步检查后报告「Python 3.12.10 · 13 款 · 49 张官方效果图 · 原生图片编解码正常 / 主接口／备用接口／AI配置均已就绪；
   飞书配置：已就绪」，因 8787 被开发实例占用 → **自动选 8788** 启动成功，`/api/health` ok；随后关闭、端口释放。
   `bootstrap.ps1 -Repair -CheckOnly`（=「一键检查修复.bat」）也跑通，退出码 0。

## 给同事的三句话

1. 整个 ZIP 解压到可写目录（建议 `D:\生图工作台`）；
2. 双击 `一键启动.bat`，等浏览器自动打开；
3. 保持窗口开着用，关窗口即停；坏了双击 `一键检查修复.bat`。

详细文案见 `releases/发给同事_9.4.4.txt`。

## 注意

- `releases/` 在 `.gitignore` 里，87 MB 的包不进仓库；要备份请单独保存 zip。
- 同事机器只需 Windows 10/11 x64（普通用户权限即可）；图像接口与 AI 需要联网。
- 包内含内部 API 密钥，只供团队内部流转，别外发。
- 升级换版：先停旧程序 → 解压新版到新目录 →（要旧历史）复制旧 `workbench\data` 过去 → 启动新版。
