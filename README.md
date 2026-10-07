# conector-copilot-zabbix

API que permite ao Copilot consultar informações específicas do Zabbix.

## Objetivo

Expor um conjunto pequeno e seguro de consultas ao Zabbix (somente leitura) para que um assistente Copilot possa responder perguntas sobre o monitoramento: hosts, incidentes, disponibilidade, alertas críticos e gráficos, incluindo VMs e hosts do VMware vSphere 8.

## Arquitetura

```
Copilot  ──HTTPS + X-API-Key──▶  conector-copilot-zabbix (k8s)  ──JSON-RPC──▶  Zabbix API (api_jsonrpc.php)
```

- Python 3.13 + FastAPI. O conector fala com a API JSON-RPC padrão do Zabbix.
- Autentica no Zabbix com o token de API de um usuário somente leitura. A versão do Zabbix é detectada na primeira chamada: na 6.4+ o token vai no cabeçalho `Authorization`, de 5.4 a 6.2 vai no corpo da requisição.
- O cliente só aceita métodos `*.get` e `apiinfo.version`; qualquer método de escrita é recusado antes de sair da API (`app/zabbix.py`).
- O Copilot nunca fala direto com o Zabbix; só com os endpoints do conector, usando o cabeçalho `X-API-Key`.
- Respostas resumidas e com `limite` (padrão 50, máximo 200) para caber no contexto do assistente. Quando o limite é atingido, a resposta traz `possivelmente_truncado: true`.

## Endpoints

Todos exigem o cabeçalho `X-API-Key`, exceto `/health` e `/ready`.

| Endpoint | O que devolve | Filtros |
|---|---|---|
| `GET /hosts` | Hosts com tipo (vm, hypervisor, vcenter, outro), grupos, IPs, status e manutenção | `busca`, `grupo`, `tipo`, `limite` |
| `GET /incidentes` | Problemas ativos, mais recentes primeiro, com severidade, hosts e duração | `severidade_min` (0–5), `host`, `grupo`, `apenas_nao_reconhecidos`, `limite` |
| `GET /alertas-criticos` | Problemas ativos de severidade Alta (4) e Desastre (5) | `host`, `grupo`, `apenas_nao_reconhecidos`, `limite` |
| `GET /disponibilidade` | Resumo (total, disponíveis, indisponíveis, %), resumo por tipo e lista dos hosts com problema | `host`, `grupo`, `tipo`, `apenas_problemas`, `limite` |
| `GET /graficos` | Sem `grafico_id`: gráficos do host com link. Com `grafico_id`: mínimo, média, máximo, último valor e até 24 pontos por série | `host`, `busca`, `grafico_id`, `horas` (até 720), `limite` |
| `GET /hosts-from-hostgroup` | Hosts de um grupo | `grupo` (obrigatório), `tipo`, `limite` |
| `GET /hostgroups` | Grupos de hosts e quantidade de hosts, para descobrir o nome a usar nos filtros | `busca`, `limite` |
| `GET /health`, `GET /ready` | Liveness e readiness (o `/ready` consulta a versão do Zabbix) | |

Documentação interativa em `/docs` quando a API está rodando.

### VMware vSphere 8

O tipo do host vem dos templates oficiais do Zabbix: `VMware Guest` → `vm`, `VMware Hypervisor` → `hypervisor`, `VMware` / `VMware FQDN` → `vcenter`. Hosts VMware não têm interface, então em `/disponibilidade`:

- VMs usam o item `vmware.vm.powerstate` (ligada = disponível, desligada = indisponível, suspensa = desconhecido);
- hypervisors usam `vmware.hv.status` (verde = disponível, amarelo = degradado, vermelho = indisponível);
- os demais hosts usam a disponibilidade das interfaces (agente, SNMP, IPMI, JMX).

### Gráficos

O Copilot não exibe a imagem do gráfico, então `/graficos` devolve os números (a partir de `trend.get`, médias por hora) e um link `chart2.php` para abrir o gráfico na interface do Zabbix, que exige login.

## Permissões no Zabbix

Crie um usuário com papel do tipo *User* e permissão de **leitura** nos grupos de hosts e de templates que o Copilot pode ver (os grupos dos templates VMware são necessários para o filtro `tipo`). Gere o token de API desse usuário.

## Configuração

Copie `.env.example` para `.env` e preencha. Nunca faça commit do `.env`.

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ZABBIX_URL` | sim | URL da API, ex.: `https://zabbix.exemplo.com/api_jsonrpc.php` |
| `ZABBIX_API_TOKEN` | sim | Token do usuário somente leitura |
| `CONNECTOR_API_KEY` | sim | Chave que o Copilot envia no cabeçalho `X-API-Key` |
| `ZABBIX_VERIFY_SSL` | não | `true` (padrão) ou `false` para certificado autoassinado |
| `ZABBIX_TIMEOUT` | não | Segundos (padrão 15) |
| `DEFAULT_LIMIT` / `MAX_LIMIT` | não | Itens por resposta (padrão 50 / máximo 200) |
| `AVAILABILITY_SCAN_MAX` | não | Teto de hosts lidos em `/disponibilidade` (padrão 5000) |

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --env-file .env --port 8080
pytest -q
```

## Kubernetes

Os manifests ficam em `k8s/`, no namespace `conector-copilot-zabbix`:

| Arquivo | Conteúdo |
|---|---|
| `00-namespace.yaml` | Namespace |
| `01-configmap.yaml` | Configurações sem segredo |
| `02-secret.example.yaml` | Exemplo do Secret (não é aplicado; crie a partir do `.env`) |
| `03-deployment.yaml` | 2 réplicas, probes, limites de recurso, usuário sem privilégio |
| `04-service.yaml` | Service ClusterIP na porta 80 |
| `05-ingress.yaml` | Ingress HTTPS em `conector-zabbix-copilot.celesc.com.br`, com o certificado do Secret TLS `celesc` |
| `06-pdb.yaml` | PodDisruptionBudget |

```bash
docker build -t celdockerrep.celesc.com.br/conector-copilot-zabbix:0.1.0 .
docker push celdockerrep.celesc.com.br/conector-copilot-zabbix:0.1.0

kubectl apply -f k8s/00-namespace.yaml
kubectl -n conector-copilot-zabbix create secret generic conector-copilot-zabbix-secrets --from-env-file=.env
kubectl apply -k k8s/
```

O Secret TLS `celesc` (certificado HTTPS) precisa existir no namespace antes do Ingress. Se o cluster não usar o ingress nginx, ajuste `ingressClassName` em `05-ingress.yaml`.

## Integração com o Copilot

`openapi/openapi.yaml` é a especificação OpenAPI 3.0.3 para importar no Copilot (plugin de API do Microsoft 365 Copilot ou ação/conector do Copilot Studio), com autenticação por chave de API no cabeçalho `X-API-Key`. Para gerar de novo com a URL pública do conector:

```bash
python scripts/export_openapi.py https://conector-zabbix-copilot.celesc.com.br
```

## Perguntas em aberto

- [ ] Versão do Zabbix. O conector suporta 5.4+ e detecta a versão sozinho; confirmar a versão real para validar.
- [x] Quais informações o Copilot deve consultar: hosts, incidentes, disponibilidade, alertas críticos, gráficos e hosts por grupo.
- [ ] Qual Copilot será usado (Microsoft 365 Copilot / Copilot Studio, GitHub Copilot, outro). A OpenAPI 3.0 atende plugins e Copilot Studio; se for GitHub Copilot, pode ser melhor expor como servidor MCP.
- [x] Linguagem e onde a API vai rodar: Python + FastAPI em Kubernetes.
