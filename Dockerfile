FROM python:3.12-slim

WORKDIR /app

# Consume build args provided by EasyPanel to prevent errors
ARG GIT_SHA
ARG YANDEX_TOKEN
ARG OPENAI_API_KEY
ARG UPLOAD_POST_API_KEY
ARG DATABASE_URL

# Install system dependencies (including ffmpeg later)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
