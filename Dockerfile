FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CERTIFYFLOW_ENV=production CERTIFYFLOW_DATA_DIR=/var/lib/certifyflow
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip && python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
COPY scripts ./scripts
RUN useradd --system --uid 10001 --create-home appuser && mkdir -p /var/lib/certifyflow app/app/fonts && chown -R appuser:appuser /app /var/lib/certifyflow
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "10.89.0.0/24"]
