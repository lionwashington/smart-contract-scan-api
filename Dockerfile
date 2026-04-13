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

# 创建非 root 用户（solc-select 状态需归属 appuser，否则运行时 Slither 找不到 solc）
RUN useradd -m -u 1000 appuser

# 复制应用代码
COPY app/ ./app/
RUN chown -R appuser:appuser /app

USER appuser

# 以 appuser 身份安装 solc-select 状态到 /home/appuser/.solc-select
RUN solc-select install 0.8.20 && solc-select use 0.8.20
ENV PATH="/home/appuser/.local/bin:${PATH}"

EXPOSE 8000

# Railway 会通过 $PORT 环境变量指定端口；uvicorn 读取该变量
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
