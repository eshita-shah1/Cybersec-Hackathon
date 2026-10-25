# Shared image for BOTH the D3COY gateway and the sandbox executor (same codebase, different
# uvicorn target chosen by the compose `command`). CPU-only PyTorch keeps the image as small as
# this ML stack allows.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.hf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install CPU PyTorch first (default index would pull the large CUDA build), then the rest.
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# Application code and the pre-trained behavioral baseline artifacts.
COPY app ./app
COPY sandbox_executor ./sandbox_executor
COPY agent ./agent
COPY scripts ./scripts
COPY artifacts ./artifacts

# Pre-download the semantic model so the first boot needs no network and starts fast.
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')"

EXPOSE 8000 8100

# Overridden per service in docker-compose.yml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
