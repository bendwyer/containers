# Multi-stage build for the Tailscale exit-node control plane.
#
# The shared container-build-push.yml workflow runs no test job, so the test
# suite gates the image here: the `build` stage runs lint + typecheck + vitest
# (npm run check) before compiling, so a failing test fails the image build.

FROM node:24-alpine3.24@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS build
WORKDIR /app
# Install all deps (incl. dev) for the gate + compile. `npm ci` installs the
# locked tree exactly, so an upstream publish cannot change deps under a
# rebuild of unchanged source. Renovate moves the lockfile.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
# Source + configs; node_modules/dist are excluded via .dockerignore.
COPY . .
# Gate, then compile TypeScript to dist/.
RUN npm run check && npm run build

# Production dependencies only, in a clean layer for the runtime image.
FROM node:24-alpine3.24@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS prod-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

FROM node:24-alpine3.24@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json ./
COPY migrations ./migrations
USER node
EXPOSE 8080
CMD ["node", "dist/index.js"]
