FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ agent/
COPY eval/ eval/

ENTRYPOINT ["python3", "agent/pipeline.py"]
