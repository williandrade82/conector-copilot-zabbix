"""Cliente JSON-RPC do Zabbix, restrito a métodos de leitura."""

import itertools
import re
from typing import Any

import httpx

# Só métodos de consulta. Qualquer outro método é recusado antes de sair da API.
_READ_ONLY_METHOD = re.compile(r"^[a-z]+\.get$")
_EXTRA_ALLOWED = {"apiinfo.version"}


class ZabbixError(Exception):
    """Erro devolvido pelo Zabbix ou falha de comunicação."""


class ForbiddenMethodError(ZabbixError):
    """Tentativa de chamar um método que não é somente leitura."""


def is_read_only(method: str) -> bool:
    return bool(_READ_ONLY_METHOD.match(method)) or method in _EXTRA_ALLOWED


def _parse_version(version: str) -> tuple[int, int]:
    parts = version.split(".")
    return int(parts[0]), int(parts[1])


class ZabbixClient:
    def __init__(self, url: str, token: str, http: httpx.AsyncClient):
        self._url = url
        self._token = token
        self._http = http
        self._ids = itertools.count(1)
        self._version: tuple[int, int] | None = None

    async def version(self) -> tuple[int, int]:
        if self._version is None:
            raw = await self._call("apiinfo.version", {}, auth=False)
            self._version = _parse_version(raw)
        return self._version

    async def get(self, method: str, params: dict[str, Any]) -> Any:
        if not is_read_only(method):
            raise ForbiddenMethodError(f"Método não permitido (somente leitura): {method}")
        return await self._call(method, params, auth=True)

    async def host_groups_param(self) -> str:
        """Nome do parâmetro que traz os grupos do host (mudou na 6.2)."""
        return "selectHostGroups" if await self.version() >= (6, 2) else "selectGroups"

    async def _call(self, method: str, params: dict[str, Any], auth: bool) -> Any:
        if not is_read_only(method):
            raise ForbiddenMethodError(f"Método não permitido (somente leitura): {method}")

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._ids),
        }
        headers = {"Content-Type": "application/json-rpc"}
        if auth:
            # Zabbix 6.4+ aceita o token no cabeçalho; 5.4 a 6.2 só no corpo.
            if await self.version() >= (6, 4):
                headers["Authorization"] = f"Bearer {self._token}"
            else:
                payload["auth"] = self._token

        try:
            response = await self._http.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            # Não inclui URL nem token na mensagem.
            raise ZabbixError(f"Falha ao contatar o Zabbix ({type(exc).__name__})") from None
        except ValueError:
            raise ZabbixError("Resposta inválida do Zabbix") from None

        if "error" in body:
            err = body["error"]
            raise ZabbixError(f"{err.get('message', 'Erro')}: {err.get('data', '')}".strip(": "))
        return body.get("result")
