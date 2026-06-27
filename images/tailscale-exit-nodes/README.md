# tailscale-exit-nodes

Control plane for self-service Tailscale exit-node deployments with a TTL. A
headless Node process that exposes a JSON API, provisions exit nodes on
**AWS Lightsail** or **Vultr**, and runs a reaper that tears them down when their
TTL elapses. Image: `ghcr.io/bendwyer/containers/tailscale-exit-nodes`.

It ships no UI of its own — pair the API with any front-end (a form UI, CLI, or
automation). It is meant to run on a private network with no ingress of its own.
The reaper runs in-process, so TTL teardown does not depend on an external
scheduler.

## How it works

- **POST /deploy** resolves a region slug, inserts a `provisioning` row, and
  kicks the provisioning saga (mint ephemeral Tailscale key -> create instance +
  firewall -> poll for readiness -> mark `active`).
- **The reaper** sweeps both clouds every 5 minutes, reconciling live instances
  against the ledger. TTL is enforced entirely by the reaper via a self-describing
  `expires-at:<unix-ms>` instance tag, so even an orphan with no ledger row is
  reaped. **DELETE /deployments/:id** clamps a row's expiry to now to trigger an
  early teardown.
- **The regions catalog** is generated at runtime from live provider data
  (Vultr `/v2/regions` + the AWS Lightsail pricing index), cached ~5h in memory,
  with a baked fallback snapshot.

State lives in Postgres (`DATABASE_URL`); the schema is applied on boot.

## Endpoints

| Method | Path               | Purpose                              |
| ------ | ------------------ | ------------------------------------ |
| POST   | `/deploy`          | `{ region, name, ttl_hours }` -> 202 |
| GET    | `/list`            | All deployments                      |
| DELETE | `/deployments/:id` | Clamp expiry (early teardown) -> 202 |
| GET    | `/regions`         | Region catalog (for the deploy form) |
| POST   | `/prune`           | Run a reaper sweep on demand         |
| GET    | `/healthz`         | Liveness (process up)                |
| GET    | `/readyz`          | Readiness (DB reachable)             |

The deploy endpoint reads an optional `X-Requested-By` header for attribution
(a front-end can pass the acting user).

## Configuration (env)

| Var                                                           | Source                       |
| ------------------------------------------------------------- | ---------------------------- |
| `DATABASE_URL`                                                | Postgres connection string   |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`                 | Lightsail IAM key            |
| `VULTR_API_KEY`                                               | Vultr account API key        |
| `TAILSCALE_OAUTH_CLIENT_ID` / `TAILSCALE_OAUTH_CLIENT_SECRET` | Tailscale OAuth client       |
| `PORT`                                                        | Listen port (default `8080`) |

## Development

```sh
npm install
npm run check   # lint + typecheck + test
npm run build   # tsc -> dist/
```

Tests use `vitest` with `pg-mem` for the data layer and a mocked `fetch` for the
cloud clients. The image build runs `npm run check` as a gate before compiling.
