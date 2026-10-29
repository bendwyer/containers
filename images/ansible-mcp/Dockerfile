FROM node:24-alpine3.24@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS build

ARG VERSION
WORKDIR /app
RUN npm install @ansible/ansible-mcp-server@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:51eee69b25037942014428da85f5b436a4d88f283e545981d03f25b7db72469a
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
