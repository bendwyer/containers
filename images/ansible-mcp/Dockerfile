FROM node:24-alpine3.24@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS build

ARG VERSION
WORKDIR /app
RUN npm install @ansible/ansible-mcp-server@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:ef765e8933cd4f7dab32b1b0899b1fab51a961508f90c064905e2e54d86f7b99
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
