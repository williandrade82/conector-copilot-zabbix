# CLAUDE.md

Contexto para agentes que trabalham neste repositório.

## Projeto

API conector entre o Copilot e o Zabbix. O Copilot faz perguntas; a API consulta a API JSON-RPC do Zabbix e devolve respostas resumidas.

## Regras

- Idioma: documentação, commits e comunicação em português (PT-BR). Nomes no código podem ser em inglês.
- Acesso ao Zabbix é somente leitura. Não implementar métodos que criem, alterem ou apaguem nada no Zabbix.
- Segredos (URL, token do Zabbix, chaves da API) apenas por variáveis de ambiente. Nunca em código, logs ou commits.
- Limitar o volume das respostas (paginação, filtros, campos selecionados) para não estourar o contexto do Copilot.
- Toda mudança via branch e pull request.

## Decisões pendentes

Stack definida: Python + FastAPI, Kubernetes (namespace `conector-copilot-zabbix`, manifests em `k8s/`), OpenAPI 3.0 em `openapi/`. Ainda em aberto (ver README): versão do Zabbix e qual Copilot.

## Comandos

- Testes: `pytest -q`
- Regenerar a OpenAPI depois de mudar endpoints: `python scripts/export_openapi.py` (o CI falha se `openapi/openapi.yaml` estiver desatualizado).
