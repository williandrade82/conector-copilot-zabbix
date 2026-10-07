"""Gera openapi/openapi.yaml (OpenAPI 3.0.3) para importar no Copilot.

Uso: python scripts/export_openapi.py [URL_PUBLICA_DO_CONECTOR]

O FastAPI gera OpenAPI 3.1; vários consumidores (plugins do Microsoft 365 Copilot,
conectores do Copilot Studio) esperam 3.0, então os campos anuláveis são convertidos.
"""

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CONNECTOR_API_KEY", "export")

from app.main import create_app  # noqa: E402


def to_30(node):
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and {"type": "null"} in any_of:
            rest = [to_30(x) for x in any_of if x != {"type": "null"}]
            node = {k: v for k, v in node.items() if k != "anyOf"}
            if len(rest) == 1 and "$ref" not in rest[0]:
                node.update(rest[0])
            else:
                node["anyOf"] = rest
            node["nullable"] = True
        return {k: to_30(v) for k, v in node.items()}
    if isinstance(node, list):
        return [to_30(x) for x in node]
    return node


def main() -> None:
    server = sys.argv[1] if len(sys.argv) > 1 else "https://conector-zabbix.exemplo.com"
    spec = to_30(create_app().openapi())
    spec["openapi"] = "3.0.3"
    spec["servers"] = [{"url": server}]
    # Rotas de saúde não interessam ao Copilot.
    for path in ("/health", "/ready"):
        spec["paths"].pop(path, None)
    out = ROOT / "openapi" / "openapi.yaml"
    out.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Gerado {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
