# 服装改款换色工作台 —— 容器镜像
# 构建（在仓库根目录 E:\picture_auto 下执行，需要同时包含 skill 目录）：
#   docker build -f workbench/deploy/Dockerfile -t apparel-workbench .
# 运行：
#   docker run -p 7860:7860 --env-file workbench/.env -v %CD%/workbench/data:/app/workbench/data apparel-workbench
#
# 说明：镜像里同时包含 apparel-style-recolor/（skill 本体）和 workbench/，因为工作台启动时会解析
# SKILL.md 与颜色参考图；data 目录建议挂卷，否则容器重启后任务历史会丢。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKBENCH_HOST=0.0.0.0 \
    WORKBENCH_PORT=7860

WORKDIR /app

# Pillow 需要的基础库（slim 镜像没有）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g libpng16-16 curl \
    && rm -rf /var/lib/apt/lists/*

COPY workbench/requirements.txt /app/workbench/requirements.txt
RUN pip install --no-cache-dir -r /app/workbench/requirements.txt

COPY apparel-style-recolor /app/apparel-style-recolor
COPY workbench /app/workbench

WORKDIR /app/workbench

# 健康检查用公开端点（带 token 的部署可自行改）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${WORKBENCH_PORT}/api/health" >/dev/null || exit 1

EXPOSE 7860

CMD ["python", "serve.py"]
