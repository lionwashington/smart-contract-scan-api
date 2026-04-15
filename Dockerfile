# ---- Mirror args (override locally for fast CN builds; default = upstream for Railway/CI) ----
ARG APT_MIRROR=http://deb.debian.org
ARG APT_SECURITY_MIRROR=http://security.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple

# ---- 构建阶段 ----
FROM python:3.11-slim-bookworm AS builder
ARG APT_MIRROR
ARG APT_SECURITY_MIRROR
ARG PIP_INDEX_URL

WORKDIR /app

# 应用 apt 镜像（仅当 ARG 非默认值时生效；bookworm 用 debian.sources 新格式，slim 回退到 sources.list）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g; s|http://security.debian.org|${APT_SECURITY_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g; s|http://security.debian.org|${APT_SECURITY_MIRROR}|g" /etc/apt/sources.list; \
    fi

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --prefix=/install -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.11-slim-bookworm
ARG APT_MIRROR
ARG APT_SECURITY_MIRROR
ARG PIP_INDEX_URL

WORKDIR /app

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g; s|http://security.debian.org|${APT_SECURITY_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g; s|http://security.debian.org|${APT_SECURITY_MIRROR}|g" /etc/apt/sources.list; \
    fi

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
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" slither-analyzer solc-select

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
