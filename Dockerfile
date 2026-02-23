# TimeSense – AI Builders Space (https://{service_name}.ai-builders.space)
# Single process: FastAPI serves API + static. PORT is set at runtime by Koyeb.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
