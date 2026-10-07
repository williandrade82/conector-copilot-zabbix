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

Ver "Perguntas em aberto" no README: versão do Zabbix, dados a consultar e qual Copilot. Não escolher stack nem formato de integração antes dessas respostas.
