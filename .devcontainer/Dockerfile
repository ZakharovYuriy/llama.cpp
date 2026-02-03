ARG CUDA_IMAGE=nvidia/cuda:12.3.2-devel-ubuntu22.04
FROM ${CUDA_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ENV CODEX_HOME=/root/.codex
ENV PATH="/root/.codex/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git git-lfs \
    cmake build-essential ninja-build pkg-config \
    ca-certificates curl libcurl4-openssl-dev \
    python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN git lfs install

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg apt-transport-https \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir -U pip setuptools wheel \
 && python3 -m pip install --no-cache-dir numpy tqdm sentencepiece safetensors huggingface_hub

WORKDIR /workspace
CMD ["bash"]
