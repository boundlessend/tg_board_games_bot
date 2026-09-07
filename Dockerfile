# образ закреплён по digest: тег 3.14-slim переезжает между сборками
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/db/bot.sqlite3

WORKDIR /app

COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

COPY . .

# непривилегированный пользователь; /db - том для персистентной базы
# (DATABASE_PATH уже указывает в него, named volume берёт права из образа)
RUN useradd --create-home app && mkdir -p /db /backups && chown app /db /backups
USER app

# отметку живости обновляет сам бот: зависший polling так тоже отловится
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "health.py"]

CMD ["python", "bot.py"]
