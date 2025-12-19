FROM python:3.11-slim

# Create non-root user with same UID as builder for shared volume permissions
RUN useradd -m -s /bin/bash -u 2026 appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1

USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
