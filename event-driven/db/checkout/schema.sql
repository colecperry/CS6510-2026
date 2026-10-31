-- The checkout service's own database: baskets, what went into them, and the messages this service owes the rest of the system.
--
-- Stock is deliberately absent. It belongs to inventory, in a different database this service holds no credentials for, which is why a sale is announced rather than applied here.

-- Full rebuild, so this script is safe to re-run between test runs.
DROP TABLE IF EXISTS outbox CASCADE;
DROP TABLE IF EXISTS transaction_items CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS catalog CASCADE;
DROP SEQUENCE IF EXISTS tx_seq;

CREATE SEQUENCE tx_seq;

-- A read-only copy of the catalogue, so a scan can return a name and price without calling inventory.
-- Safe to duplicate only because catalogue rows never change while the system is running.
CREATE TABLE catalog (
  sku   TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

-- One row per checkout transaction (a customer's visit to a station).
CREATE TABLE transactions (
  transaction_id TEXT PRIMARY KEY DEFAULT ('tx-' || nextval('tx_seq')),
  station_id     TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','COMPLETED','CANCELLED')),
  item_count     INTEGER NOT NULL DEFAULT 0,
  running_total  NUMERIC(12,2) NOT NULL DEFAULT 0,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at   TIMESTAMPTZ
);

-- One row per scanned unit. Carries the name and price at the time of sale, so a receipt shows what was actually charged.
CREATE TABLE transaction_items (
  id             BIGSERIAL PRIMARY KEY,
  transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
  sku            TEXT NOT NULL,
  name           TEXT NOT NULL,
  unit_price     NUMERIC(10,2) NOT NULL,
  scanned_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON transaction_items (transaction_id);

-- Messages this service has committed to sending but may not have sent yet.
--
-- This database and the broker are two separate systems, and a write to two systems cannot be made atomic. Committing then publishing loses the message if the process dies in between; publishing then committing announces a sale that never happened. Writing the message in here, inside the same transaction as the sale, means there is only ever one commit and nothing to lose.
CREATE TABLE outbox (
  -- Travels with the message. The consumer deduplicates on this, so a message sent twice is applied once.
  message_id  UUID PRIMARY KEY,
  routing_key TEXT NOT NULL,
  payload     JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Null until the broker has confirmed receipt. The relay asks only for these.
  sent_at     TIMESTAMPTZ
);

-- Partial, because the relay only ever reads unsent rows and that set is normally empty. A full index would grow with every sale ever made for no benefit.
CREATE INDEX outbox_unsent ON outbox (created_at) WHERE sent_at IS NULL;
