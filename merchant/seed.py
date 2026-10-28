"""确定性演示数据。只在显式初始化时写库；问答模块永远只读。"""
import sqlite3
from pathlib import Path

from merchant.config import DB_PATH, DEMO_TODAY

PRODUCTS = [
    ("P001", "云朵纯棉T恤", "服饰", 7900, 3500, 40),
    ("P002", "轻行帆布包", "箱包", 5900, 2200, 30),
    ("P003", "晨光保温杯", "家居", 12900, 5500, 30),
    ("P004", "桌面收纳盒", "家居", 3900, 1400, 25),
    ("P005", "无线蓝牙耳机", "数码", 19900, 11000, 20),
    ("P006", "舒眠眼罩", "服饰", 2900, 800, 30),
]
STOCK = [(120, 15), (60, 8), (25, 5), (85, 10), (12, 2), (100, 12)]

SCHEMA = """
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    list_price_cents INTEGER NOT NULL CHECK (list_price_cents >= 0),
    current_cost_cents INTEGER NOT NULL CHECK (current_cost_cents >= 0),
    safety_stock INTEGER NOT NULL CHECK (safety_stock >= 0)
);
CREATE TABLE orders (
    item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('paid','completed','refunded','cancelled')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    unit_cost_cents INTEGER NOT NULL CHECK(unit_cost_cents >= 0)
);
CREATE TABLE inventory (
    product_id TEXT PRIMARY KEY REFERENCES products(product_id),
    snapshot_date TEXT NOT NULL,
    on_hand INTEGER NOT NULL CHECK(on_hand >= 0),
    reserved INTEGER NOT NULL CHECK(reserved >= 0 AND reserved <= on_hand)
);
CREATE INDEX idx_orders_date_status ON orders(order_date, status);
CREATE INDEX idx_orders_product ON orders(product_id);
"""


def demo_orders():
    for month_index, month in enumerate((7, 8, 9, 10)):
        for product_index, product in enumerate(PRODUCTS, 1):
            pid, _, _, price, cost, _ = product
            quantity = (month_index + 1) * 3 + product_index * 2
            for status, day, qty in (("paid", 5, quantity), ("completed", 15, quantity + 2),
                                     ("cancelled", 17, 1), ("refunded", 20, 2)):
                oid = f"DEMO-{month:02d}-{product_index:02d}-{status}"
                yield (f"{oid}-1", oid, pid, f"2026-{month:02d}-{day:02d}", status,
                       qty, price - month_index * 100, cost + month_index * 50)


def seed_database(path=DB_PATH):
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # 避免把未完成的初始化文件当成可用库。
    temp = path.with_suffix(".building")
    try:
        with sqlite3.connect(temp) as con:
            con.execute("PRAGMA foreign_keys=ON")
            con.executescript(SCHEMA)
            con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", PRODUCTS)
            con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", demo_orders())
            con.executemany("INSERT INTO inventory VALUES (?,?,?,?)",
                            [(p[0], DEMO_TODAY, *stock) for p, stock in zip(PRODUCTS, STOCK)])
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    created = seed_database()
    print(f"{'已生成' if created else '已存在，保留原文件'}：{DB_PATH}（全部为演示数据）")
