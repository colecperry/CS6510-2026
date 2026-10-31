-- The analytics service's own database: the stream of scans and the ranking built from it.
--
-- Checkout cannot write here. Scans arrive as batched HTTP posts from checkout instead, which is why this service can fall behind, or be stopped entirely, without a shopper noticing.

DROP TABLE IF EXISTS popularity_snapshot CASCADE;
DROP TABLE IF EXISTS popularity_state CASCADE;
DROP TABLE IF EXISTS scan_log CASCADE;
DROP TABLE IF EXISTS catalog CASCADE;
DROP SEQUENCE IF EXISTS scan_seq;

CREATE SEQUENCE scan_seq;

-- A read-only copy of the catalogue, so the ranking can show item names without calling inventory.
CREATE TABLE catalog (
  sku   TEXT PRIMARY KEY,
  name  TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL
);

-- Every scanned unit, as a global stream. Separate from checkout's basket records so old rows can be trimmed here without ever touching data a receipt still needs.
CREATE TABLE scan_log (
  seq        BIGINT PRIMARY KEY DEFAULT nextval('scan_seq'),
  sku        TEXT NOT NULL,
  scanned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Single-row bookkeeping for the hopping window: which stretch of the stream the current ranking covers.
CREATE TABLE popularity_state (
  id             INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  window_size    INT NOT NULL DEFAULT 1000,
  slide_interval INT NOT NULL DEFAULT 500,
  window_start   BIGINT NOT NULL DEFAULT 0,
  window_end     BIGINT NOT NULL DEFAULT 0,
  computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO popularity_state (id) VALUES (1);

-- The current ranking, replaced wholesale on each rebuild rather than appended to.
CREATE TABLE popularity_snapshot (
  rank       INT PRIMARY KEY,
  sku        TEXT NOT NULL,
  name       TEXT NOT NULL,
  scan_count BIGINT NOT NULL
);
