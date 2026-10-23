-- The checkout service's own database: baskets, what went into them, and the progress of payments.
--
-- Stock is deliberately absent. It belongs to inventory, in a different database this service holds no credentials for, which is why paying has to be coordinated rather than done in one transaction.

-- Full rebuild, so this script is safe to re-run between test runs.
DROP TABLE IF EXISTS payment_saga CASCADE;
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
  completed_at   TIMESTAMPTZ,
  -- Set while a payment is in flight, and never sent over the wire. The API contract fixes status to the three values above, so "being paid for" cannot be a fourth one.
  -- It does two jobs: it stops a second payment claiming the same basket, and it stops an item being scanned in after the receipt lines were already worked out.
  paying_since   TIMESTAMPTZ
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

-- How far one payment got. Without this, a crash between "stock reduced" and "basket marked paid" is unrecoverable, because nothing afterwards could tell a payment that never started from one half done.
CREATE TABLE payment_saga (
  -- Sent to inventory as its idempotency key. Inventory has no concept of a saga and treats it as an opaque string.
  saga_id        TEXT PRIMARY KEY,
  -- Unique, so one basket can never have two payments running at once.
  transaction_id TEXT NOT NULL UNIQUE REFERENCES transactions(transaction_id),
  state          TEXT NOT NULL CHECK (state IN ('PENDING','APPLIED','DONE','COMPENSATED')),
  -- The SKUs and quantities this payment must reduce, frozen when the basket was claimed, so compensation knows exactly what to put back.
  lines          JSONB NOT NULL,
  -- Telemetry only. Nothing branches on it; the retry budget lives in the service.
  attempts       INTEGER NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Recovery at startup only cares about payments that never reached an end state, so the index covers just those.
CREATE INDEX payment_saga_unfinished ON payment_saga (created_at) WHERE state IN ('PENDING','APPLIED');
