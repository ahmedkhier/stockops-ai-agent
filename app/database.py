"""SQLite inventory catalog and stock ledger operations."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, List, Optional
from app.schemas import ExtractedInvoice

DEFAULT_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/inventory.db")

INITIAL_CATALOG_SEEDS = [
    ("LED-PNL-6060-40W", "Backlit LED Panel 60x60cm 40W 6500K", 120, 14.50),
    ("FL-COB-100W-IP65", "Outdoor Floodlight 100W IP65 Aluminum", 45, 28.00),
    ("DRV-24V-150W-IP67", "Waterproof LED Driver 24V 150W", 80, 19.20),
    ("CBL-H07RN-F-3G1.5", "Heavy Duty Rubber Cable 3x1.5mm (100m Roll)", 15, 85.00),
]


def resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve active database path from parameter or environment."""
    path = db_path or os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection with dict-like row access, safely closing on exit."""
    target_path = resolve_db_path(db_path)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database schema and seed initial catalog items."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory_catalog (
                sku TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                current_stock INTEGER NOT NULL DEFAULT 0,
                avg_unit_cost REAL NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                sku TEXT NOT NULL,
                quantity_added INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.executemany(
            """
            INSERT OR IGNORE INTO inventory_catalog (sku, name, current_stock, avg_unit_cost)
            VALUES (?, ?, ?, ?)
            """,
            INITIAL_CATALOG_SEEDS,
        )
        conn.commit()


def get_historical_unit_cost(sku: str, db_path: Optional[str] = None) -> Optional[float]:
    """Retrieve historical average unit cost for a given SKU."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT avg_unit_cost FROM inventory_catalog WHERE sku = ?",
            (sku,),
        )
        row = cursor.fetchone()
        if row:
            return float(row["avg_unit_cost"])
        return None


def commit_invoice_to_stock(
    invoice: ExtractedInvoice, status: str, db_path: Optional[str] = None
) -> None:
    """Commit invoice line items to stock ledger and update catalog stock if AUTO_COMMITTED."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        for item in invoice.items:
            # 1. Always record in stock ledger for auditability
            cursor.execute(
                """
                INSERT INTO stock_ledger (invoice_number, sku, quantity_added, unit_price, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    invoice.invoice_number,
                    item.sku,
                    item.quantity,
                    item.unit_price,
                    status,
                ),
            )

            # 2. Only increment catalog stock when approved/auto-committed
            if status == "AUTO_COMMITTED":
                cursor.execute(
                    """
                    UPDATE inventory_catalog
                    SET current_stock = current_stock + ?
                    WHERE sku = ?
                    """,
                    (item.quantity, item.sku),
                )

        conn.commit()


def get_inventory_catalog(db_path: Optional[str] = None) -> List[Dict]:
    """Fetch all inventory catalog records."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sku, name, current_stock, avg_unit_cost FROM inventory_catalog ORDER BY sku")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_stock_ledger(db_path: Optional[str] = None) -> List[Dict]:
    """Fetch all stock ledger records."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, invoice_number, sku, quantity_added, unit_price, status, created_at FROM stock_ledger ORDER BY id DESC"
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
