FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/db/bot.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# непривилегированный пользователь; /db - том для персистентной базы
# (DATABASE_PATH уже указывает в него, named volume берёт права из образа)
RUN useradd --create-home app && mkdir -p /db /backups && chown app /db /backups
USER app

VOLUME ["/db"]

# отметку живости обновляет сам бот: зависший polling так тоже отловится
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "health.py"]

CMD ["python", "bot.py"]
