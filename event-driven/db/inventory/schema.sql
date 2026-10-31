-- The inventory service's own database: what the shop sells and how much of it is left.
--
-- This is the only place stock exists. Checkout cannot reach it and cannot call it, so stock comes down when this service hears that a sale happened.

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

-- Every sale this service has already acted on, keyed by the id the message carried.
--
-- The broker promises to deliver a message at least once, which means sometimes twice: a sender can publish, die before recording that it did, and publish again on restart. Writing the id in here, in the same transaction as the stock change, means the second copy finds the row already present and does nothing.
CREATE TABLE applied_requests (
  -- Opaque to this service. It is checkout's outbox row id, but nothing here depends on where it came from.
  message_id UUID PRIMARY KEY,
  -- What this message moved. Kept for auditing a stock figure back to the sale that caused it.
  lines      JSONB NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
