FROM node:24-alpine3.24@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS build

ARG VERSION
WORKDIR /app
RUN npm install @upstash/context7-mcp@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:7cca079bad19303c78cd874a5da79832441985a216b767196507d69b8784a698
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@upstash/context7-mcp/dist/index.js"]
CMD ["--help"]
