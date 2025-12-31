# Build Stage
FROM node:20-alpine AS builder

# Support build args (optional, but good practice to accept them if passed)
ARG YANDEX_TOKEN
ARG OPENAI_API_KEY
ARG UPLOAD_POST_API_KEY
ARG GIT_SHA

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY tsconfig.json ./
# Copy example config so it's available for build/runtime fallback
COPY config.example.json ./config.example.json
COPY src ./src
COPY public ./public

RUN npm run build

# Production Stage
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/public ./public
COPY --from=builder /app/config.example.json ./config.example.json

# Environment variables for runtime
ENV PORT=3001
ENV DATA_DIR=/app/data

# Expose default port
EXPOSE 3001

CMD ["npm", "start"]
