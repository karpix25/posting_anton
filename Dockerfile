# Build Stage
FROM node:20-alpine AS builder

# Only accept GIT_SHA as build arg (not sensitive)
ARG GIT_SHA

WORKDIR /app

# 1. Install Backend Dependencies
COPY package*.json ./
RUN npm install

# 2. Build Frontend
COPY frontend ./frontend
WORKDIR /app/frontend
# Explicitly install frontend deps and build
RUN npm install
RUN npm run build
WORKDIR /app

# 3. Build Backend
COPY tsconfig.json ./
COPY config.example.json ./config.example.json
COPY src ./src
# Note: We do NOT copy valid old public folder here, we will replace it
RUN npm run build

# Production Stage
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY --from=builder /app/dist ./dist
# Copy Frontend Build to public/
COPY --from=builder /app/frontend/dist ./public
COPY --from=builder /app/config.example.json ./config.example.json

# Environment variables for runtime
ENV PORT=3001
ENV DATA_DIR=/app/data

# Expose default port
EXPOSE 3001

CMD ["npm", "start"]
