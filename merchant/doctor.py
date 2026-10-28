"""只检查本地环境，不输出密钥、不调用 API、不改数据库。"""
import importlib.metadata
import json
import platform
import sqlite3

from merchant.config import DB_PATH, DEMO_TODAY, load_settings
from merchant.database import run_readonly


def inspect_environment():
    settings = load_settings()
    report = {
        "python": platform.python_version(), "system": platform.system(),
        "architecture": platform.machine(), "sqlite": sqlite3.sqlite_version,
        "packages": {name: importlib.metadata.version(name) for name in
                     ("streamlit", "langchain", "langchain-openai", "sqlglot", "pandas")},
        "database_exists": DB_PATH.is_file(), "data_label": "全部为虚构演示数据",
        "demo_reference_date": DEMO_TODAY,
        "api_key_configured": settings.ready, "real_api_called_by_this_command": False,
        "model": settings.model,
    }
    saved_test = DB_PATH.parents[1] / "reports" / "live-connection.json"
    if saved_test.is_file():
        try:
            saved = json.loads(saved_test.read_text(encoding="utf-8"))
            report["last_saved_connection_test"] = {
                key: saved[key] for key in ("status", "passed", "model", "verified_at_shanghai") if key in saved
            }
            report["connection_test_note"] = "历史测试记录，当前命令不重新验证 API 或变更后的配置。"
        except (ValueError, OSError):
            report["connection_test_note"] = "无法读取历史测试记录；当前命令不调用 API。"
    if DB_PATH.is_file():
        report["table_rows"] = {table: run_readonly(f"SELECT COUNT(*) FROM {table}").rows[0][0]
                                for table in ("orders", "products", "inventory")}
    return report


if __name__ == "__main__":
    print(json.dumps(inspect_environment(), ensure_ascii=False, indent=2))
