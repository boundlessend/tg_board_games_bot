FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# том для персистентной базы: запускать с -v <host>:/db и DATABASE_PATH=/db/bot.sqlite3
VOLUME ["/db"]

CMD ["python", "bot.py"]
