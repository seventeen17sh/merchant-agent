from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import os

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "demo_store.sqlite3"
DOC_PATH = ROOT / "docs" / "业务指标说明.md"
DEMO_TODAY = "2026-10-20"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_timeout: float = 45
    sql_timeout: float = 2
    max_rows: int = 100

    @property
    def ready(self):
        return bool(self.api_key.strip())


def load_settings(path=None):
    values = {**dotenv_values(path or ROOT / ".env"), **os.environ}
    try:
        settings = Settings(
            api_key=values.get("DEEPSEEK_API_KEY", "") or "",
            base_url=(values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
            model=values.get("DEEPSEEK_MODEL") or "deepseek-flash",
            api_timeout=float(values.get("API_TIMEOUT_SECONDS", 45)),
            sql_timeout=float(values.get("SQL_TIMEOUT_SECONDS", 2)),
            max_rows=int(values.get("MAX_RESULT_ROWS", 100)),
        )
    except (ValueError, TypeError) as exc:
        raise ConfigurationError("配置中的超时或行数必须是数字。请检查 .env。") from exc
    parsed = urlparse(settings.base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("API 地址必须是有效的 HTTPS 地址，不应包含账号、查询参数或片段。")
    if not 1 <= settings.max_rows <= 500 or not 0.001 <= settings.sql_timeout <= 10 or not 1 <= settings.api_timeout <= 120:
        raise ConfigurationError("配置范围：返回行数 1—500，SQL 超时 0.001—10 秒，API 超时 1—120 秒。")
    if not settings.model.strip():
        raise ConfigurationError("模型名称不能为空。")
    return settings
