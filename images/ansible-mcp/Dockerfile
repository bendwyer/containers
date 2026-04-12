FROM node:25-alpine AS build

WORKDIR /app
RUN npm install @ansible/ansible-mcp-server

FROM gcr.io/distroless/nodejs24-debian12
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
