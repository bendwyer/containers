FROM node:26-alpine3.24@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build

ARG VERSION
WORKDIR /app
RUN npm install @ansible/ansible-mcp-server@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:24bc0fe0225c7518f202a822587dad74fe143b96326b81fe5cc08c1df8bed03e
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
