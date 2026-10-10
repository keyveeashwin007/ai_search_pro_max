# Backend image for Render / Fly / Railway. The front end is deployed separately (Netlify).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

COPY config.yaml serve_web.py ./
COPY data ./data
COPY src ./src

# /data holds the SQLite cache (mount a disk/volume there to keep it). Runs as root on purpose:
# platform disks are often mounted root-owned and a non-root user then cannot write the cache.
RUN mkdir /data
ENV TXN_CACHE_PATH=/data/cache.db HOST=0.0.0.0 PORT=8600

EXPOSE 8600
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8600')+'/api/health')" || exit 1

# TXN_ACCESS_PASSWORD is required (the server refuses to start without it).
CMD ["python", "serve_web.py"]
