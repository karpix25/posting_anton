# Build Stage
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY tsconfig.json ./
COPY src ./src
COPY public ./public
# Config should be mounted or created, but we can copy the default if it exists
# COPY config.json ./config.json 

RUN npm run build

# Production Stage
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/public ./public

# Ensure config directory works. We expect config.json to be mounted or created.
# If running locally without mount, copy default.
# COPY config.json ./config.json

# Expose default port (can be overridden)
EXPOSE 3001

CMD ["npm", "start"]
