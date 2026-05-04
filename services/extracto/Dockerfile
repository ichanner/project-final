FROM node:25-alpine AS base

WORKDIR /app

RUN apk upgrade --no-cache \
    && apk add --no-cache curl tini \
    && addgroup -S app && adduser -S app -G app

COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY src ./src

USER app

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8081/health || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "src/index.js"]
