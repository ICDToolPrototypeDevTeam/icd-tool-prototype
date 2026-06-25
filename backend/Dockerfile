FROM python:3.11-slim

WORKDIR /app

# Use Aliyun mirrors for faster downloads in China
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources

# LibreOffice for .doc -> .docx conversion
# Uses BuildKit cache mount to avoid re-downloading package lists on rebuilds
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# BuildKit cache mount preserves pip downloads across rebuilds
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    -r requirements.txt

COPY . .
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]