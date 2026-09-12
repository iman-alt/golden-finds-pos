import sqlite3

DB_NAME = "golden_finds.db"


def get_connection():
    """
    Opens a connection to the database.
    row_factory lets us access columns by name (e.g. row['name'])
    instead of just by position (row[1]) - much easier to read.
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # enforce foreign key rules
    return conn


def init_db():
    """
    Creates every table from the design doc. Safe to run repeatedly -
    won't wipe existing data. Tables are created in dependency order
    (a table can only reference a table that already exists).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- USERS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            pin_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'cashier')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ---------- SUPPLIERS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            contact_person TEXT
        )
    """)

    # ---------- CUSTOMERS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            is_wholesale INTEGER NOT NULL DEFAULT 0,
            credit_balance REAL NOT NULL DEFAULT 0
        )
    """)

    # ---------- PRODUCTS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            unit_type TEXT NOT NULL DEFAULT 'piece',
            retail_price REAL NOT NULL,
            wholesale_price REAL NOT NULL,
            wholesale_min_qty INTEGER NOT NULL DEFAULT 6,
            cost_price REAL NOT NULL DEFAULT 0,
            stock_quantity INTEGER NOT NULL DEFAULT 0,
            low_stock_threshold INTEGER NOT NULL DEFAULT 5,
            track_expiry INTEGER NOT NULL DEFAULT 0,
            image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_barcode ON products (barcode)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_name ON products (name)
    """)

    # ---------- BATCHES ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            supplier_id INTEGER,
            batch_number TEXT,
            cost_price REAL NOT NULL,
            quantity_received INTEGER NOT NULL,
            quantity_remaining INTEGER NOT NULL,
            expiry_date DATE,
            received_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (supplier_id) REFERENCES suppliers (id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_batches_expiry ON batches (expiry_date)
    """)

    # ---------- STOCK MOVEMENTS (the ledger) ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            batch_id INTEGER,
            quantity_change INTEGER NOT NULL,
            movement_type TEXT NOT NULL CHECK (
                movement_type IN ('stock_in','sale','return','damaged','expired','count_adjustment')
            ),
            reference_type TEXT,
            reference_id INTEGER,
            note TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (batch_id) REFERENCES batches (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    """)

    # ---------- PURCHASES ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER,
            created_by INTEGER,
            total_cost REAL NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            cost_price REAL NOT NULL,
            expiry_date DATE,
            FOREIGN KEY (purchase_id) REFERENCES purchases (id),
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    """)

    # ---------- SALES ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            cashier_id INTEGER,
            total REAL NOT NULL,
            payment_method TEXT,
            amount_paid REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (id),
            FOREIGN KEY (cashier_id) REFERENCES users (id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            batch_id INTEGER,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            was_wholesale_price INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (sale_id) REFERENCES sales (id),
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (batch_id) REFERENCES batches (id)
        )
    """)

    # ---------- RETURNS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            sale_item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            reason TEXT,
            refunded_amount REAL NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id) REFERENCES sales (id),
            FOREIGN KEY (sale_item_id) REFERENCES sale_items (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    """)

    # ---------- OFFERS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            batch_id INTEGER,
            offer_price REAL NOT NULL,
            tier TEXT CHECK (tier IN ('monitor','warning','consider_offer','urgent')),
            approved_by INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id),
            FOREIGN KEY (batch_id) REFERENCES batches (id),
            FOREIGN KEY (approved_by) REFERENCES users (id)
        )
    """)

    # ---------- PAYMENTS ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            customer_id INTEGER,
            amount REAL NOT NULL,
            method TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id) REFERENCES sales (id),
            FOREIGN KEY (customer_id) REFERENCES customers (id)
        )
    """)

    conn.commit()
    conn.close()
    print("Database ready:", DB_NAME)


if __name__ == "__main__":
    init_db()
