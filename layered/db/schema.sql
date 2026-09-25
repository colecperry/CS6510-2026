-- Full rebuild: drop everything first so this script is safe to re-run
-- as many times as we want (this is our "reset between test runs" script).
DROP TABLE IF EXISTS popularity_snapshot CASCADE;
DROP TABLE IF EXISTS popularity_state CASCADE;
DROP TABLE IF EXISTS scan_log CASCADE;
DROP TABLE IF EXISTS transaction_items CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS stock CASCADE;
DROP TABLE IF EXISTS catalog CASCADE;
DROP SEQUENCE IF EXISTS tx_seq;
DROP SEQUENCE IF EXISTS scan_seq;

CREATE SEQUENCE tx_seq;
CREATE SEQUENCE scan_seq;

-- The fixed product catalog: 2000 SKUs, name, price. Never changes during a run.
CREATE TABLE catalog (
  sku   TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

-- Current stock count per SKU. This is the row every concurrent checkout
-- fights over at completion time - see Step 16 for the safe decrement.
CREATE TABLE stock (
  sku TEXT PRIMARY KEY REFERENCES catalog(sku),
  qty INTEGER NOT NULL CHECK (qty >= 0)
);
CREATE INDEX ON stock (qty);

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

-- One row per scanned unit within a transaction - the permanent basket
-- record, used to build the receipt and drive stock decrements at completion.
CREATE TABLE transaction_items (
  id             BIGSERIAL PRIMARY KEY,
  transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
  sku            TEXT NOT NULL,
  name           TEXT NOT NULL,
  unit_price     NUMERIC(10,2) NOT NULL,
  scanned_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON transaction_items (transaction_id);

-- Separate global stream of every scan, used only for the popular-items
-- feature. Kept separate from transaction_items so we can safely trim old
-- rows here without ever touching data a still-open basket/receipt needs.
CREATE TABLE scan_log (
  seq        BIGINT PRIMARY KEY DEFAULT nextval('scan_seq'),
  sku        TEXT NOT NULL,
  scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Single-row bookkeeping for the hopping window: what window is currently
-- "active" and when it was last recomputed.
CREATE TABLE popularity_state (
  id             INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  window_size    INT NOT NULL DEFAULT 1000,
  slide_interval INT NOT NULL DEFAULT 500,
  window_start   BIGINT NOT NULL DEFAULT 0,
  window_end     BIGINT NOT NULL DEFAULT 0,
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO popularity_state (id) VALUES (1);

-- Cached top-N ranking for the current window, replaced wholesale each
-- time the window is recomputed (never appended to).
CREATE TABLE popularity_snapshot (
  rank       INT PRIMARY KEY,
  sku        TEXT NOT NULL,
  name       TEXT NOT NULL,
  scan_count BIGINT NOT NULL
);
