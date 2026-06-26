FROM python:3.12-slim

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
RUN useradd --create-home app && mkdir -p /db && chown app /db
USER app

VOLUME ["/db"]

CMD ["python", "bot.py"]
