FROM node:24-alpine3.24 AS build

WORKDIR /app
RUN npm install @ansible/ansible-mcp-server

FROM gcr.io/distroless/nodejs24-debian13
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
