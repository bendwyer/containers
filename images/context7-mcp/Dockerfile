FROM node:24-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build

ARG VERSION
WORKDIR /app
RUN npm install @upstash/context7-mcp@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:b1fc33242cc74151f50c62b4a03d48afd759dccf81279b5f8e401db4546479c1
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@upstash/context7-mcp/dist/index.js"]
CMD ["--help"]
