-- The inventory service's own database: what the shop sells and how much of it is left.
--
-- This is the only place stock exists. Checkout cannot reach it, so reducing stock at payment time happens through a request to this service rather than a shared transaction.

DROP TABLE IF EXISTS applied_requests CASCADE;
DROP TABLE IF EXISTS stock CASCADE;
DROP TABLE IF EXISTS catalog CASCADE;

-- The authoritative catalogue. Checkout and analytics hold read-only copies seeded from the same data.
CREATE TABLE catalog (
  sku   TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

-- Current units remaining per SKU. The CHECK is the last line of defence: the contract defines no out-of-stock response, so a decrement floors at zero rather than letting this constraint fire.
CREATE TABLE stock (
  sku TEXT PRIMARY KEY REFERENCES catalog(sku),
  qty INTEGER NOT NULL CHECK (qty >= 0)
);
CREATE INDEX ON stock (qty);

-- Every stock change this service has already applied, keyed by the caller's idempotency key.
--
-- Checkout retries a stock reduction when a reply goes missing, and a timeout does not mean the work did not happen. Recording the key lets a repeat be recognised and ignored instead of taking stock twice.
CREATE TABLE applied_requests (
  -- Opaque to this service. Checkout happens to send a saga id; nothing here depends on that.
  idempotency_key TEXT PRIMARY KEY,
  -- What was taken, so a later request to undo this one knows exactly what to put back.
  lines           JSONB NOT NULL,
  applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Set when the reduction has been undone, so a repeated undo is also harmless.
  restored_at     TIMESTAMPTZ
);
