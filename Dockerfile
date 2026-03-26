# ---- 构建阶段 ----
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.11-slim

WORKDIR /app

# 安装运行时依赖：
# - solc-select：管理 Solidity 编译器版本
# - git：slither 有时需要
# - curl：健康检查用
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 安装 slither-analyzer 和 solc-select
RUN pip install --no-cache-dir slither-analyzer solc-select

# 安装并设置默认的 solc 版本（0.8.x 最常用）
RUN solc-select install 0.8.20 && solc-select use 0.8.20

# 复制应用代码
COPY app/ ./app/

# 创建非 root 用户运行（安全最佳实践）
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Railway 会通过 $PORT 环境变量指定端口；uvicorn 读取该变量
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
