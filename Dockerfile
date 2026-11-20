FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --system quinuaalert && useradd --system --gid quinuaalert --home-dir /app quinuaalert
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /data/imagenes_mildiu/.staging && chown -R quinuaalert:quinuaalert /app /data

USER quinuaalert
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--no-proxy-headers"]

