"""Configuração lida apenas de variáveis de ambiente."""

import os
from dataclasses import dataclass


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass(frozen=True)
class Settings:
    zabbix_url: str
    zabbix_api_token: str
    connector_api_key: str
    zabbix_verify_ssl: bool = True
    zabbix_timeout: float = 15.0
    default_limit: int = 50
    max_limit: int = 200
    # Teto de hosts lidos para montar o resumo de disponibilidade.
    availability_scan_max: int = 5000

    @property
    def frontend_url(self) -> str:
        """URL base da interface web, derivada da URL da API."""
        url = self.zabbix_url.rstrip("/")
        if url.endswith("api_jsonrpc.php"):
            url = url[: -len("api_jsonrpc.php")]
        return url.rstrip("/")


def load_settings() -> Settings:
    return Settings(
        zabbix_url=os.environ.get("ZABBIX_URL", ""),
        zabbix_api_token=os.environ.get("ZABBIX_API_TOKEN", ""),
        connector_api_key=os.environ.get("CONNECTOR_API_KEY", ""),
        zabbix_verify_ssl=_bool(os.environ.get("ZABBIX_VERIFY_SSL"), True),
        zabbix_timeout=float(os.environ.get("ZABBIX_TIMEOUT", "15")),
        default_limit=int(os.environ.get("DEFAULT_LIMIT", "50")),
        max_limit=int(os.environ.get("MAX_LIMIT", "200")),
        availability_scan_max=int(os.environ.get("AVAILABILITY_SCAN_MAX", "5000")),
    )
