FROM node:24-alpine AS build

WORKDIR /app
RUN npm install @upstash/context7-mcp

FROM gcr.io/distroless/nodejs24-debian12
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@upstash/context7-mcp/dist/index.js"]
