FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-paddle.txt ./
ARG INSTALL_PADDLE=false
RUN if [ "$INSTALL_PADDLE" = "true" ]; then pip install --no-cache-dir -r requirements-paddle.txt; else pip install --no-cache-dir -r requirements.txt; fi
COPY app ./app
RUN useradd --create-home --uid 10001 ocr && mkdir -p /app/data && chown -R ocr:ocr /app
USER ocr
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
