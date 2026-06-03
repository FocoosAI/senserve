FROM isnob46/dgx-vllm-cu13:0211rc1

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface \
    UV_PYTHON=3.12 \
    TORCH_CUDA_ARCH_LIST=9.0a \
    FLASHINFER_CUDA_ARCH_LIST=9.0a \
    VLLM_USE_FLASHINFER_SAMPLER=0

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md config/ ./
COPY src/ ./src/

RUN uv python install 3.12 \
    && uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:/root/.local/bin:${PATH}"

EXPOSE 8787


CMD ["uv", "run", "senserve", "--load", "qwen3.5-0.8b"]
