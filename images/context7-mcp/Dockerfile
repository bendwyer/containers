FROM node:24-alpine3.24@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS build

ARG VERSION
WORKDIR /app
RUN npm install @upstash/context7-mcp@${VERSION}

FROM gcr.io/distroless/nodejs24-debian13@sha256:e7b49c7570331548957099e4c53d1b7255605dd20b0c56ee368077c07c1b170b
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@upstash/context7-mcp/dist/index.js"]
CMD ["--help"]
