# ---------------------------------------
# Stage 1: Build Frontend (Vue 3 + Vite)
# ---------------------------------------
FROM node:20-alpine AS frontend_builder

WORKDIR /app_frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend ./
# Build output goes to /app_frontend/dist
RUN npm run build


# ---------------------------------------
# Stage 2: Build Backend (Python / FastAPI)
# ---------------------------------------
FROM python:3.11-slim

WORKDIR /app

# Consume build args provided by EasyPanel
ARG GIT_SHA
ARG YANDEX_TOKEN
ARG OPENAI_API_KEY
ARG UPLOAD_POST_API_KEY
ARG DATABASE_URL

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python Source Code
# Copy 'app' package
COPY app ./app
# Copy 'migrations' if exists (ignore error if not exists by using wildcard or explicit copy)
COPY migrations ./migrations 
# Copy root scripts
COPY *.py ./

# ---------------------------------------
# Integrate Frontend
# ---------------------------------------
# Create public directory
RUN mkdir -p public

# Copy Vue Build Artifacts from Stage 1
COPY --from=frontend_builder /app_frontend/dist ./public

# ---------------------------------------
# Runtime Config
# ---------------------------------------
ENV PORT=3001
EXPOSE 3001

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
