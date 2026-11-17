FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY motor.py bot.py editor_fotos.py ./
# La base de datos va en un volumen para no perder los creditos al reiniciar.
ENV BOT_DB=/data/bot.db
VOLUME /data
CMD ["python", "bot.py"]
