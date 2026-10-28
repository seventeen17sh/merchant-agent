"""SQL AST 检查 + SQLite 只读连接、授权回调、结果上限与执行超时。"""
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
import threading
import time

import sqlglot
from sqlglot import exp

from merchant.config import DB_PATH

TABLE_COLUMNS = {
    "products": {"product_id", "product_name", "category", "list_price_cents", "current_cost_cents", "safety_stock"},
    "orders": {"item_id", "order_id", "product_id", "order_date", "status", "quantity", "unit_price_cents", "unit_cost_cents"},
    "inventory": {"product_id", "snapshot_date", "on_hand", "reserved"},
}
FUNCTIONS = {"sum", "count", "avg", "min", "max", "round", "coalesce", "ifnull", "nullif", "abs",
             "lower", "upper", "length", "substr", "substring", "date", "strftime", "datetime",
             "julianday", "trim", "like", "glob", "instr", "replace"}


class QueryRejected(ValueError):
    pass


@dataclass
class QueryResult:
    columns: list
    rows: list
    executed_sql: str
    truncated: bool
    elapsed_ms: float
    max_rows: int

    def to_dict(self):
        return asdict(self)


def validate_sql(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 12000:
        raise QueryRejected("SQL 为空或长度超过 12000 字符。")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.SqlglotError as exc:
        raise QueryRejected("SQL 语法无法通过安全检查。") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise QueryRejected("只允许一条 SELECT 查询，禁止写入、多语句和管理命令。")
    tree = statements[0]
    if len(list(tree.walk())) > 800:
        raise QueryRejected("查询过于复杂，请缩小范围。")
    # 本项目不需要 CTE、集合运算、窗口、事务或用户自定义函数。
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command,
                 exp.Union, exp.Intersect, exp.Except, exp.With, exp.Window, exp.Into)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise QueryRejected("查询包含不支持的结构；请使用普通 SELECT、JOIN、GROUP BY。")
    for table in tree.find_all(exp.Table):
        if table.name.lower() not in TABLE_COLUMNS or table.db or table.catalog:
            raise QueryRejected("只能查询 orders、products、inventory 三张演示表。")
    # 正数文字 LIMIT/OFFSET 才可接受；实际执行还会再次加外层限制。
    for node in tree.find_all(exp.Limit, exp.Offset):
        value = node.expression
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) < 0:
            raise QueryRejected("LIMIT 和 OFFSET 必须是非负整数。")
    return tree.sql(dialect="sqlite", pretty=True)


def run_readonly(sql, db_path=DB_PATH, max_rows=100, timeout=2.0):
    if not 1 <= max_rows <= 500 or not 0.001 <= timeout <= 10:
        raise QueryRejected("行数或超时参数超出允许范围。")
    safe_sql = validate_sql(sql)
    # 多取一行用于检测截断。展示的就是这条实际执行语句。
    executed = f'SELECT *\nFROM (\n{safe_sql}\n) AS "_limited_result"\nLIMIT {max_rows + 1}'
    path = Path(db_path).resolve()
    if not path.is_file():
        raise QueryRejected("演示数据库不存在，请先运行初始化。")
    start = time.monotonic()
    deadline = start + timeout
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)
    timer = None
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.enable_load_extension(False)

        def authorize(action, arg1, arg2, dbname, source):
            if time.monotonic() > deadline:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                # SQLite 对无列读取的 COUNT(*) 优化会把 dbname 传为 None。
                main_read = dbname == "main" or (dbname is None and arg2 == "")
                allowed = main_read and arg1 in TABLE_COLUMNS and (not arg2 or arg2 in TABLE_COLUMNS[arg1])
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or "").lower() in FUNCTIONS else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY

        con.set_authorizer(authorize)
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
        timer = threading.Timer(timeout, con.interrupt)
        timer.daemon = True
        timer.start()
        cursor = con.execute(executed)
        rows = cursor.fetchmany(max_rows + 1)
        return QueryResult([c[0] for c in cursor.description], [list(r) for r in rows[:max_rows]],
                           executed, len(rows) > max_rows, round((time.monotonic() - start) * 1000, 2), max_rows)
    except sqlite3.DatabaseError as exc:
        if time.monotonic() >= deadline or "interrupt" in str(exc).lower():
            raise QueryRejected(f"查询已在 {timeout:g} 秒超时中止，请缩小日期范围或减少关联。") from exc
        raise QueryRejected("查询被只读权限、字段或函数校验拒绝，或 SQL 无法执行。") from exc
    finally:
        if timer:
            timer.cancel()
            timer.join()
        con.close()


SCHEMA_DESCRIPTION = """
products(product_id TEXT, product_name TEXT, category TEXT, list_price_cents INTEGER,
         current_cost_cents INTEGER, safety_stock INTEGER)
orders(item_id TEXT, order_id TEXT, product_id TEXT, order_date TEXT 'YYYY-MM-DD',
       status TEXT 'paid'/'completed'/'refunded'/'cancelled', quantity INTEGER,
       unit_price_cents INTEGER, unit_cost_cents INTEGER)
inventory(product_id TEXT, snapshot_date TEXT 'YYYY-MM-DD', on_hand INTEGER, reserved INTEGER)
三表用 product_id 关联。orders 每行是订单明细；每笔演示订单一行；inventory 每商品仅一行快照。
金额存分。销量 SUM(quantity)；销售额 SUM(quantity*unit_price_cents)/100.0；
毛利 SUM(quantity*(unit_price_cents-unit_cost_cents))/100.0；毛利率=毛利分/销售额分*100.0。
销量/销售额/毛利只计 status IN ('paid','completed')；refunded/cancelled 全部排除。
成本用 orders.unit_cost_cents 历史成交成本；不能用 products.current_cost_cents 替代。
可售库存=on_hand-reserved；低库存：可售库存 < safety_stock（严格小于）。
库存金额=可售库存*products.current_cost_cents/100.0；库存是快照，不随订单日期变化。
订单与库存不可直接多对多聚合（避免重复累计）。查询库存只关联 products。
金额/比例 ROUND(...,2)，分母 NULLIF(...,0)，无成交 SUM 用 COALESCE(...,0)。
无时间条件默认全部订单；月份半开区间 >=月初 AND <下月初。本月为月初至参照今天（含）。
"""
