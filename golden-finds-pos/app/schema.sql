-- Golden Finds POS schema.
--
-- Conventions used throughout:
--   * Every money column is an INTEGER count of cents, suffixed _cents.
--     No REAL columns anywhere. See app/money.py for the reasoning.
--   * Every table that records something a person did carries the user id
--     that did it. A till nobody is accountable for is not a till.
--   * stock_movements is an append-only ledger. Stock levels elsewhere are
--     caches of it and can be rebuilt; the ledger itself is never updated
--     or deleted.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- USERS --
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    pin_hash    TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('admin', 'cashier')),
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login  TEXT
);

-- ------------------------------------------------------------ SUPPLIERS --
CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    phone           TEXT,
    contact_person  TEXT,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

-- ------------------------------------------------------------ CUSTOMERS --
CREATE TABLE IF NOT EXISTS customers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    phone                TEXT,
    is_wholesale         INTEGER NOT NULL DEFAULT 0 CHECK (is_wholesale IN (0, 1)),
    -- Positive means the customer owes the shop.
    credit_balance_cents INTEGER NOT NULL DEFAULT 0,
    credit_limit_cents   INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers (phone);

-- ------------------------------------------------------------- PRODUCTS --
CREATE TABLE IF NOT EXISTS products (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode               TEXT    NOT NULL UNIQUE,
    name                  TEXT    NOT NULL,
    category              TEXT,
    unit_type             TEXT    NOT NULL DEFAULT 'piece',
    retail_price_cents    INTEGER NOT NULL CHECK (retail_price_cents > 0),
    wholesale_price_cents INTEGER NOT NULL CHECK (wholesale_price_cents > 0),
    wholesale_min_qty     INTEGER NOT NULL DEFAULT 6 CHECK (wholesale_min_qty > 0),
    cost_price_cents      INTEGER NOT NULL DEFAULT 0 CHECK (cost_price_cents >= 0),
    -- Cache of the ledger. Rebuildable via services.stock.recalculate_stock.
    stock_quantity        INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold   INTEGER NOT NULL DEFAULT 5,
    track_expiry          INTEGER NOT NULL DEFAULT 0 CHECK (track_expiry IN (0, 1)),
    image_path            TEXT,
    active                INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by            INTEGER REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS idx_products_name     ON products (name);
CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);

-- -------------------------------------------------------------- BATCHES --
-- One delivery of one product. Only created for expiry-tracked products;
-- FEFO consumption sorts on expiry_date.
CREATE TABLE IF NOT EXISTS batches (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER NOT NULL REFERENCES products (id),
    supplier_id        INTEGER REFERENCES suppliers (id),
    batch_number       TEXT,
    cost_price_cents   INTEGER NOT NULL CHECK (cost_price_cents >= 0),
    quantity_received  INTEGER NOT NULL CHECK (quantity_received > 0),
    quantity_remaining INTEGER NOT NULL CHECK (quantity_remaining >= 0),
    expiry_date        TEXT,
    received_date      TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by         INTEGER REFERENCES users (id),
    CHECK (quantity_remaining <= quantity_received)
);
CREATE INDEX IF NOT EXISTS idx_batches_fefo ON batches (product_id, expiry_date)
    WHERE quantity_remaining > 0;

-- --------------------------------------------- STOCK MOVEMENTS (ledger) --
CREATE TABLE IF NOT EXISTS stock_movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products (id),
    batch_id        INTEGER REFERENCES batches (id),
    quantity_change INTEGER NOT NULL CHECK (quantity_change <> 0),
    movement_type   TEXT    NOT NULL CHECK (movement_type IN
                        ('stock_in','sale','return','damaged','expired',
                         'count_adjustment','void')),
    reference_type  TEXT,
    reference_id    INTEGER,
    note            TEXT,
    created_by      INTEGER REFERENCES users (id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_movements_product ON stock_movements (product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_movements_ref     ON stock_movements (reference_type, reference_id);

-- ------------------------------------------------------------ PURCHASES --
CREATE TABLE IF NOT EXISTS purchases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id      INTEGER REFERENCES suppliers (id),
    invoice_number   TEXT,
    total_cost_cents INTEGER NOT NULL DEFAULT 0,
    created_by       INTEGER REFERENCES users (id),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS purchase_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id      INTEGER NOT NULL REFERENCES purchases (id),
    product_id       INTEGER NOT NULL REFERENCES products (id),
    batch_id         INTEGER REFERENCES batches (id),
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    cost_price_cents INTEGER NOT NULL CHECK (cost_price_cents >= 0),
    expiry_date      TEXT
);

-- ---------------------------------------------------------------- OFFERS --
-- An offer is always an explicit, admin-approved price. The system never
-- sets one on its own; it only surfaces which batches are worth discounting.
CREATE TABLE IF NOT EXISTS offers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id        INTEGER NOT NULL REFERENCES products (id),
    batch_id          INTEGER REFERENCES batches (id),
    offer_price_cents INTEGER NOT NULL CHECK (offer_price_cents > 0),
    tier              TEXT CHECK (tier IN ('monitor','warning','consider_offer','urgent','expired')),
    approved_by       INTEGER NOT NULL REFERENCES users (id),
    active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    start_date        TEXT    NOT NULL DEFAULT (datetime('now')),
    end_date          TEXT,
    ended_by          INTEGER REFERENCES users (id),
    ended_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_lookup ON offers (product_id, active);

-- ----------------------------------------------------------------- SALES --
CREATE TABLE IF NOT EXISTS sales (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_number     TEXT    NOT NULL UNIQUE,
    customer_id        INTEGER REFERENCES customers (id),
    cashier_id         INTEGER NOT NULL REFERENCES users (id),
    subtotal_cents     INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    discount_cents     INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
    total_cents        INTEGER NOT NULL CHECK (total_cents >= 0),
    amount_paid_cents  INTEGER NOT NULL DEFAULT 0 CHECK (amount_paid_cents >= 0),
    change_cents       INTEGER NOT NULL DEFAULT 0 CHECK (change_cents >= 0),
    payment_method     TEXT    NOT NULL CHECK (payment_method IN ('cash','mpesa','credit')),
    status             TEXT    NOT NULL DEFAULT 'completed'
                               CHECK (status IN ('completed','voided')),
    voided_by          INTEGER REFERENCES users (id),
    voided_at          TEXT,
    void_reason        TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sales_created ON sales (created_at);
CREATE INDEX IF NOT EXISTS idx_sales_cashier ON sales (cashier_id, created_at);

CREATE TABLE IF NOT EXISTS sale_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id             INTEGER NOT NULL REFERENCES sales (id),
    product_id          INTEGER NOT NULL REFERENCES products (id),
    batch_id            INTEGER REFERENCES batches (id),
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    quantity_returned   INTEGER NOT NULL DEFAULT 0 CHECK (quantity_returned >= 0),
    unit_price_cents    INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    line_total_cents    INTEGER NOT NULL CHECK (line_total_cents >= 0),
    -- What decided the price, so a receipt can explain itself later.
    price_basis         TEXT    NOT NULL DEFAULT 'retail'
                                CHECK (price_basis IN ('retail','wholesale','offer')),
    offer_id            INTEGER REFERENCES offers (id),
    -- Cost at time of sale, frozen so profit reports stay true after the
    -- product's cost price is later changed.
    unit_cost_cents     INTEGER NOT NULL DEFAULT 0,
    CHECK (quantity_returned <= quantity)
);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale    ON sale_items (sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product ON sale_items (product_id);

-- --------------------------------------------------------------- RETURNS --
CREATE TABLE IF NOT EXISTS returns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id         INTEGER NOT NULL REFERENCES sales (id),
    sale_item_id    INTEGER NOT NULL REFERENCES sale_items (id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    reason          TEXT,
    -- Whether the goods came back saleable, or went to the bin.
    restocked       INTEGER NOT NULL DEFAULT 1 CHECK (restocked IN (0, 1)),
    refunded_cents  INTEGER NOT NULL CHECK (refunded_cents >= 0),
    created_by      INTEGER NOT NULL REFERENCES users (id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_returns_sale ON returns (sale_id);

-- -------------------------------------------------------------- PAYMENTS --
CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id       INTEGER REFERENCES sales (id),
    customer_id   INTEGER REFERENCES customers (id),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents <> 0),
    method        TEXT    NOT NULL CHECK (method IN ('cash','mpesa','credit')),
    note          TEXT,
    created_by    INTEGER REFERENCES users (id),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments (customer_id, created_at);

-- ------------------------------------------------------------- AUDIT LOG --
-- Anything sensitive that is not a stock movement: logins, price changes,
-- voids, offer approvals, user management.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users (id),
    action      TEXT    NOT NULL,
    entity_type TEXT,
    entity_id   INTEGER,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at);

-- ------------------------------------------------------- PINNED PAIRINGS --
-- Pairings the owner has decided on herself, kept regardless of what the
-- sales data says. Discovered pairings are counted from sale_items and
-- are not stored. product_a is always the lower id, so a pair can only
-- ever exist once.
CREATE TABLE IF NOT EXISTS pinned_pairings (
    product_a  INTEGER NOT NULL REFERENCES products (id),
    product_b  INTEGER NOT NULL REFERENCES products (id),
    note       TEXT,
    created_by INTEGER REFERENCES users (id),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (product_a, product_b),
    CHECK (product_a < product_b)
);

-- ------------------------------------------------------------------ DENI --
-- Goods taken now and paid for later. One row per item handed over. The
-- goods leave the shelf the moment the deni is recorded, exactly like a
-- sale; only the money comes later. A person is identified by their phone
-- number, stored normalised as 07XXXXXXXX / 01XXXXXXXX.
CREATE TABLE IF NOT EXISTS deni (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name     TEXT    NOT NULL,
    phone             TEXT    NOT NULL,
    product_id        INTEGER NOT NULL REFERENCES products (id),
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    total_cents       INTEGER NOT NULL CHECK (total_cents >= 0),
    paid_cents        INTEGER NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
    status            TEXT    NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'paid', 'cancelled')),
    taken_on          TEXT    NOT NULL,
    created_by        INTEGER NOT NULL REFERENCES users (id),
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    cancelled_by      INTEGER REFERENCES users (id),
    cancelled_at      TEXT,
    cancel_reason     TEXT,
    CHECK (paid_cents <= total_cents)
);
CREATE INDEX IF NOT EXISTS idx_deni_phone ON deni (phone, status);
CREATE INDEX IF NOT EXISTS idx_deni_taken ON deni (taken_on);

-- Each repayment, whatever entries it was spread across.
CREATE TABLE IF NOT EXISTS deni_payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    phone          TEXT    NOT NULL,
    customer_name  TEXT,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    method         TEXT    NOT NULL CHECK (method IN ('cash', 'mpesa')),
    received_by    INTEGER NOT NULL REFERENCES users (id),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_deni_payments_phone ON deni_payments (phone);

-- -------------------------------------------------------------- SETTINGS --
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
