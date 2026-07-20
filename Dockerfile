FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY static/ static/

ENV ARCHITECTOS_HOST=0.0.0.0 ARCHITECTOS_PORT=8321
EXPOSE 8321

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8321"]
