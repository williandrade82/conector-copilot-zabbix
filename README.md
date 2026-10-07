# conector-copilot-zabbix

API que permite ao Copilot consultar informações específicas do Zabbix.

## Objetivo

Expor um conjunto pequeno e seguro de consultas ao Zabbix (somente leitura) para que um assistente Copilot possa responder perguntas sobre o monitoramento, como status de hosts, problemas ativos e métricas recentes.

## Arquitetura (proposta)

```
Copilot  ──HTTP/plugin/MCP──▶  conector-copilot-zabbix  ──JSON-RPC──▶  Zabbix API
```

- O conector autentica no Zabbix com um token de API de um usuário somente leitura.
- O Copilot nunca fala direto com o Zabbix; só com os endpoints do conector.
- Respostas resumidas e filtradas, para caber no contexto do assistente.

## Perguntas em aberto

- [ ] Versão do Zabbix (define a autenticação: token de API a partir da 5.4, `user.login` antes disso).
- [ ] Quais informações o Copilot deve consultar (ex.: problemas ativos, status de hosts, histórico de itens, triggers).
- [ ] Qual Copilot será usado (Microsoft 365 Copilot / Copilot Studio, GitHub Copilot, outro). Isso define o formato de integração: OpenAPI, plugin ou servidor MCP.
- [ ] Linguagem e onde a API vai rodar.

## Configuração

Copie `.env.example` para `.env` e preencha. Nunca faça commit do `.env`.
