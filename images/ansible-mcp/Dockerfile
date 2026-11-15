FROM node:26-alpine3.24@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build

ARG VERSION
WORKDIR /app
RUN npm install @ansible/ansible-mcp-server@${VERSION}

# The Node line is part of the image name, so Renovate refreshes the digest but
# never moves the line; keep it on the build stage's line by hand.
FROM gcr.io/distroless/nodejs26-debian13@sha256:80631bd2f0e7ef192138a7409013b47d92fd11b8ff3bb35f5c0a65be3ecaea4c
COPY --from=build /app /app
WORKDIR /app
ENTRYPOINT ["/nodejs/bin/node", "node_modules/@ansible/ansible-mcp-server/dist/cli.js"]
