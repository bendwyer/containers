FROM node:24-alpine3.24@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS build

WORKDIR /app
RUN npm install @upstash/context7-mcp

FROM gcr.io/distroless/nodejs24-debian13@sha256:7cca079bad19303c78cd874a5da79832441985a216b767196507d69b8784a698
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@upstash/context7-mcp/dist/index.js"]
CMD ["--help"]
