FROM python:3.12-slim

# Mencegah file .pyc dan memastikan log Python langsung tampil di Arcane
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1. Install git untuk fitur clone/pull di Web UI dan bersihkan cache apt
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# 2. Copy dan install dependencies lebih dulu agar memanfaatkan Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy kode aplikasi di tahap akhir (edit kode tidak akan memicu ulang pip install)
COPY ui.py server.py ./

# Siapkan direktori volume
RUN mkdir -p /data/skills

EXPOSE 8000 8001

CMD ["python", "server.py"]
