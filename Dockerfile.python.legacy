FROM python:3.11-slim

WORKDIR /app

# Consume build args provided by EasyPanel to prevent errors
ARG GIT_SHA
ARG YANDEX_TOKEN
ARG OPENAI_API_KEY
ARG UPLOAD_POST_API_KEY
ARG DATABASE_URL

# Install system dependencies (including ffmpeg later)
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command
ENV PORT=3001
EXPOSE 3001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
