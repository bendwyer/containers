-- Deployments ledger: one row per provisioned exit node.
--
-- The server inserts a `provisioning` row when POST /deploy starts a saga, the
-- provision saga updates it to `active` (or `failed`), and the reaper reconciles
-- it during its sweep — destroying instances whose `expires_at` has elapsed and
-- flagging cloud instances with no matching row (orphans).
--
-- Applied on boot by db.ts (CREATE ... IF NOT EXISTS), so the schema is the
-- source of truth and a fresh Postgres database self-initializes.

CREATE TABLE IF NOT EXISTS deployments (
  -- UUID; also the provision saga's id, so a row and its saga share an id.
  id              TEXT PRIMARY KEY,
  -- User-supplied label and the friendly region slug the deploy was requested with.
  name            TEXT NOT NULL,
  region_slug     TEXT NOT NULL,
  -- Resolved from the regions catalog at deploy time. provider is 'aws' or 'vultr';
  -- provider_region is that cloud's native code (e.g. 'eu-central-1', 'fra').
  provider        TEXT NOT NULL,
  provider_region TEXT NOT NULL,
  -- Node hostname advertised to the tailnet: `${name}-${short id}`.
  hostname        TEXT NOT NULL,
  -- 'provisioning' | 'active' | 'failed' | 'destroyed'.
  status          TEXT NOT NULL,
  -- Cloud instance handle the reaper deletes by: Lightsail instance name (with
  -- provider_region) or Vultr instance id. NULL until the instance is created.
  instance_ref    TEXT,
  -- Vultr firewall-group id, kept so the reaper can delete it alongside the
  -- instance. NULL for Lightsail (firewall is per-instance public ports).
  firewall_ref    TEXT,
  -- Public IP, filled once the instance is ready. Last error message if failed.
  public_ip       TEXT,
  error           TEXT,
  -- Unix epoch milliseconds (Date.now()). The reaper destroys a node once
  -- now >= expires_at; DELETE /deployments/:id clamps expires_at down to now.
  created_at      BIGINT NOT NULL,
  expires_at      BIGINT NOT NULL
);

-- The reaper's core sweep is "rows whose expires_at is in the past and which
-- aren't already destroyed"; index both columns it filters on.
CREATE INDEX IF NOT EXISTS idx_deployments_expires_at ON deployments (expires_at);
CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments (status);
