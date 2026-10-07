"""API conector entre o Copilot e o Zabbix (somente leitura)."""

import logging
import secrets
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app import vmware
from app.config import Settings, load_settings
from app.models import (
    DadosGrafico,
    Disponibilidade,
    Grafico,
    GrupoHosts,
    Host,
    HostDisponibilidade,
    HostsDoGrupo,
    Incidente,
    ListaGraficos,
    ListaGrupos,
    ListaHosts,
    ListaIncidentes,
    Ponto,
    ResumoDisponibilidade,
    Serie,
)
from app.zabbix import ZabbixClient, ZabbixError

# O httpx registra a URL de cada requisição em INFO; a URL do Zabbix é tratada como segredo.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("conector")

SEVERIDADES = {
    0: "Não classificada",
    1: "Informação",
    2: "Atenção",
    3: "Média",
    4: "Alta",
    5: "Desastre",
}
INTERFACE_AVAILABLE = {"0": "desconhecido", "1": "disponivel", "2": "indisponivel"}

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Chave do conector")


def _iso(clock: str | int) -> str:
    return datetime.fromtimestamp(int(clock), tz=timezone.utc).isoformat()


def _duration(seconds: int) -> str:
    days, rest = divmod(max(seconds, 0), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = [f"{days}d"] if days else []
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            timeout=settings.zabbix_timeout,
            verify=settings.zabbix_verify_ssl,
            transport=transport,
        ) as http:
            app.state.zabbix = ZabbixClient(settings.zabbix_url, settings.zabbix_api_token, http)
            yield

    app = FastAPI(
        title="Conector Copilot Zabbix",
        version="0.1.0",
        description=(
            "Consultas somente leitura ao Zabbix para o Copilot: hosts, incidentes, "
            "disponibilidade, alertas críticos e gráficos. Inclui hosts VMware vSphere "
            "(VMs, hypervisors e vCenter) monitorados pelos templates oficiais."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(ZabbixError)
    async def zabbix_error_handler(_: Request, exc: ZabbixError):
        logger.warning("Erro do Zabbix: %s", exc)
        return JSONResponse(status_code=502, content={"detail": f"Erro ao consultar o Zabbix: {exc}"})

    async def require_api_key(key: Annotated[str | None, Security(api_key_header)]) -> None:
        if not settings.connector_api_key:
            raise HTTPException(503, "CONNECTOR_API_KEY não configurada")
        if not key or not secrets.compare_digest(key, settings.connector_api_key):
            raise HTTPException(401, "Chave de API inválida")

    def zbx(request: Request) -> ZabbixClient:
        return request.app.state.zabbix

    Zbx = Annotated[ZabbixClient, Depends(zbx)]
    Limite = Annotated[
        int | None,
        Query(ge=1, le=settings.max_limit, description=f"Máximo de itens (padrão {settings.default_limit})"),
    ]
    protected = [Depends(require_api_key)]

    def limit_of(limite: int | None) -> int:
        return limite or settings.default_limit

    # ---------- auxiliares de filtro ----------

    async def find_groups(z: ZabbixClient, nome: str) -> list[dict[str, Any]]:
        """Grupo pelo nome exato; se não houver, busca parcial."""
        params = {"output": ["groupid", "name"], "selectHosts": "count"}
        groups = await z.get("hostgroup.get", {**params, "filter": {"name": [nome]}})
        if not groups:
            groups = await z.get("hostgroup.get", {**params, "search": {"name": nome}, "limit": 20})
        if not groups:
            raise HTTPException(404, f"Grupo de hosts não encontrado: {nome}")
        return groups

    async def group_ids(z: ZabbixClient, nome: str | None) -> list[str] | None:
        if not nome:
            return None
        return [g["groupid"] for g in await find_groups(z, nome)]

    async def host_ids(z: ZabbixClient, nome: str | None) -> list[str] | None:
        if not nome:
            return None
        hosts = await z.get(
            "host.get",
            {
                "output": ["hostid"],
                "search": {"host": nome, "name": nome},
                "searchByAny": True,
                "limit": settings.max_limit,
            },
        )
        if not hosts:
            raise HTTPException(404, f"Host não encontrado: {nome}")
        return [h["hostid"] for h in hosts]

    async def vmware_template_ids(z: ZabbixClient, tipo: str) -> list[str]:
        templates = await z.get(
            "template.get",
            {"output": ["templateid", "name"], "search": {"name": "VMware"}, "startSearch": True},
        )
        return [t["templateid"] for t in templates if vmware.classify_template(t["name"]) == tipo]

    async def query_hosts(
        z: ZabbixClient,
        *,
        limit: int,
        busca: str | None = None,
        groupids: list[str] | None = None,
        hostids: list[str] | None = None,
        tipo: str | None = None,
        only_monitored: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "output": ["hostid", "host", "name", "status", "maintenance_status"],
            "selectParentTemplates": ["name"],
            "selectInterfaces": ["ip", "dns", "available"],
            await z.host_groups_param(): ["name"],
            "sortfield": "name",
            "limit": limit,
        }
        if busca:
            params["search"] = {"host": busca, "name": busca}
            params["searchByAny"] = True
        if groupids:
            params["groupids"] = groupids
        if hostids:
            params["hostids"] = hostids
        if only_monitored:
            params["monitored_hosts"] = True
        if tipo and tipo != vmware.TIPO_OUTRO:
            templateids = await vmware_template_ids(z, tipo)
            if not templateids:
                return []
            params["templateids"] = templateids
        hosts = await z.get("host.get", params)
        if tipo == vmware.TIPO_OUTRO:
            hosts = [h for h in hosts if vmware.classify_host(h.get("parentTemplates", [])) == tipo]
        return hosts

    def to_host(h: dict[str, Any]) -> Host:
        interfaces = h.get("interfaces", [])
        states = {INTERFACE_AVAILABLE.get(str(i.get("available")), "desconhecido") for i in interfaces}
        if "indisponivel" in states:
            disponibilidade = "indisponivel"
        elif states == {"disponivel"}:
            disponibilidade = "disponivel"
        else:
            disponibilidade = "desconhecido"
        groups = h.get("hostgroups", h.get("groups", []))
        return Host(
            id=h["hostid"],
            host=h["host"],
            nome=h["name"],
            status="ativo" if str(h["status"]) == "0" else "desativado",
            em_manutencao=str(h.get("maintenance_status", "0")) == "1",
            tipo=vmware.classify_host(h.get("parentTemplates", [])),
            grupos=[g["name"] for g in groups],
            ips=[i.get("ip") or i.get("dns") for i in interfaces if i.get("ip") or i.get("dns")],
            disponibilidade=disponibilidade,
        )

    async def query_problems(
        z: ZabbixClient,
        *,
        severities: list[int],
        limit: int,
        groupids: list[str] | None,
        hostids: list[str] | None,
        nao_reconhecidos: bool,
    ) -> ListaIncidentes:
        params: dict[str, Any] = {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "suppressed"],
            "source": 0,
            "object": 0,
            "recent": False,
            "severities": severities,
            "sortfield": ["eventid"],
            "sortorder": "DESC",
            "limit": limit,
        }
        if groupids:
            params["groupids"] = groupids
        if hostids:
            params["hostids"] = hostids
        if nao_reconhecidos:
            params["acknowledged"] = False
        problems = await z.get("problem.get", params)

        hosts_by_trigger: dict[str, list[str]] = {}
        triggerids = sorted({p["objectid"] for p in problems})
        if triggerids:
            triggers = await z.get(
                "trigger.get",
                {"output": ["triggerid"], "triggerids": triggerids, "selectHosts": ["name"], "preservekeys": True},
            )
            for tid, trig in triggers.items():
                hosts_by_trigger[tid] = [h["name"] for h in trig.get("hosts", [])]

        now = int(time.time())
        incidentes = []
        for p in problems:
            nivel = int(p["severity"])
            incidentes.append(
                Incidente(
                    id=p["eventid"],
                    nome=p["name"],
                    severidade=SEVERIDADES.get(nivel, str(nivel)),
                    severidade_nivel=nivel,
                    hosts=hosts_by_trigger.get(p["objectid"], []),
                    inicio=_iso(p["clock"]),
                    duracao=_duration(now - int(p["clock"])),
                    reconhecido=str(p.get("acknowledged", "0")) == "1",
                    suprimido=str(p.get("suppressed", "0")) == "1",
                )
            )
        return ListaIncidentes(
            retornados=len(incidentes),
            possivelmente_truncado=len(incidentes) >= limit,
            por_severidade=dict(Counter(i.severidade for i in incidentes)),
            incidentes=incidentes,
        )

    def graph_url(graphid: str, horas: int = 24) -> str:
        return f"{settings.frontend_url}/chart2.php?graphid={graphid}&from=now-{horas}h&to=now"

    # ---------- saúde ----------

    @app.get("/health", tags=["saude"], summary="Liveness")
    async def health():
        return {"status": "ok"}

    @app.get("/ready", tags=["saude"], summary="Readiness: verifica o acesso ao Zabbix")
    async def ready(z: Zbx):
        major, minor = await z.version()
        return {"status": "ok", "zabbix": f"{major}.{minor}"}

    # ---------- consultas ----------

    @app.get(
        "/hosts",
        response_model=ListaHosts,
        dependencies=protected,
        tags=["consultas"],
        operation_id="listarHosts",
        summary="Lista hosts monitorados, com filtros por nome, grupo e tipo VMware",
    )
    async def hosts(
        z: Zbx,
        busca: Annotated[str | None, Query(description="Parte do nome do host")] = None,
        grupo: Annotated[str | None, Query(description="Nome do grupo de hosts")] = None,
        tipo: Annotated[
            str | None, Query(pattern="^(vm|hypervisor|vcenter|outro)$", description="vm, hypervisor, vcenter ou outro")
        ] = None,
        limite: Limite = None,
    ):
        limit = limit_of(limite)
        raw = await query_hosts(z, limit=limit, busca=busca, groupids=await group_ids(z, grupo), tipo=tipo)
        return ListaHosts(retornados=len(raw), possivelmente_truncado=len(raw) >= limit, hosts=[to_host(h) for h in raw])

    @app.get(
        "/incidentes",
        response_model=ListaIncidentes,
        dependencies=protected,
        tags=["consultas"],
        operation_id="listarIncidentes",
        summary="Problemas ativos (não resolvidos), do mais recente para o mais antigo",
    )
    async def incidentes(
        z: Zbx,
        severidade_min: Annotated[int, Query(ge=0, le=5, description="0 Não classificada a 5 Desastre")] = 0,
        host: Annotated[str | None, Query(description="Parte do nome do host")] = None,
        grupo: Annotated[str | None, Query(description="Nome do grupo de hosts")] = None,
        apenas_nao_reconhecidos: bool = False,
        limite: Limite = None,
    ):
        return await query_problems(
            z,
            severities=list(range(severidade_min, 6)),
            limit=limit_of(limite),
            groupids=await group_ids(z, grupo),
            hostids=await host_ids(z, host),
            nao_reconhecidos=apenas_nao_reconhecidos,
        )

    @app.get(
        "/alertas-criticos",
        response_model=ListaIncidentes,
        dependencies=protected,
        tags=["consultas"],
        operation_id="listarAlertasCriticos",
        summary="Problemas ativos de severidade Alta e Desastre",
    )
    async def alertas_criticos(
        z: Zbx,
        host: Annotated[str | None, Query(description="Parte do nome do host")] = None,
        grupo: Annotated[str | None, Query(description="Nome do grupo de hosts")] = None,
        apenas_nao_reconhecidos: bool = False,
        limite: Limite = None,
    ):
        return await query_problems(
            z,
            severities=[4, 5],
            limit=limit_of(limite),
            groupids=await group_ids(z, grupo),
            hostids=await host_ids(z, host),
            nao_reconhecidos=apenas_nao_reconhecidos,
        )

    @app.get(
        "/disponibilidade",
        response_model=Disponibilidade,
        dependencies=protected,
        tags=["consultas"],
        operation_id="consultarDisponibilidade",
        summary="Disponibilidade atual dos hosts ativos (interfaces e estado VMware)",
        description=(
            "Para VMs usa o estado de energia (vmware.vm.powerstate); para hypervisors, o status "
            "geral do ESXi (vmware.hv.status); para os demais, a disponibilidade das interfaces."
        ),
    )
    async def disponibilidade(
        z: Zbx,
        host: Annotated[str | None, Query(description="Parte do nome do host")] = None,
        grupo: Annotated[str | None, Query(description="Nome do grupo de hosts")] = None,
        tipo: Annotated[
            str | None, Query(pattern="^(vm|hypervisor|vcenter|outro)$", description="vm, hypervisor, vcenter ou outro")
        ] = None,
        apenas_problemas: Annotated[
            bool, Query(description="true lista só os hosts não disponíveis; false lista todos")
        ] = True,
        limite: Limite = None,
    ):
        scan = settings.availability_scan_max
        raw = await query_hosts(
            z,
            limit=scan,
            groupids=await group_ids(z, grupo),
            hostids=await host_ids(z, host),
            tipo=tipo,
            only_monitored=True,
        )
        ids = [h["hostid"] for h in raw]

        vmware_state: dict[str, tuple[str, str]] = {}
        if ids:
            for key in (vmware.KEY_VM_POWERSTATE, vmware.KEY_HV_STATUS):
                items = await z.get(
                    "item.get",
                    {
                        "output": ["hostid", "key_", "lastvalue"],
                        "hostids": ids,
                        "search": {"key_": key},
                        "startSearch": True,
                        "monitored": True,
                    },
                )
                for item in items:
                    vmware_state[item["hostid"]] = vmware.availability_from_vmware(item["key_"], item["lastvalue"])

        rows: list[HostDisponibilidade] = []
        for h in raw:
            base = to_host(h)
            if h["hostid"] in vmware_state:
                estado, detalhe = vmware_state[h["hostid"]]
            elif h.get("interfaces"):
                estado, detalhe = base.disponibilidade, "interfaces do host"
            else:
                estado, detalhe = "desconhecido", "sem interface nem item VMware"
            rows.append(HostDisponibilidade(id=base.id, nome=base.nome, tipo=base.tipo, disponibilidade=estado, detalhe=detalhe))

        def summarize(items: list[HostDisponibilidade]) -> ResumoDisponibilidade:
            c = Counter(r.disponibilidade for r in items)
            total = len(items)
            return ResumoDisponibilidade(
                total=total,
                disponivel=c["disponivel"],
                indisponivel=c["indisponivel"],
                degradado=c["degradado"],
                desconhecido=c["desconhecido"],
                percentual_disponivel=round(100 * c["disponivel"] / total, 2) if total else 0.0,
            )

        por_tipo = {t: summarize([r for r in rows if r.tipo == t]) for t in vmware.TIPOS if any(r.tipo == t for r in rows)}
        listed = [r for r in rows if r.disponibilidade != "disponivel"] if apenas_problemas else rows
        # Problemas primeiro, para que o limite não esconda o que importa.
        order = {"indisponivel": 0, "degradado": 1, "desconhecido": 2, "disponivel": 3}
        listed.sort(key=lambda r: (order.get(r.disponibilidade, 9), r.nome))
        return Disponibilidade(
            resumo=summarize(rows),
            por_tipo=por_tipo,
            hosts_analisados_truncado=len(raw) >= scan,
            hosts=listed[: limit_of(limite)],
        )

    @app.get(
        "/graficos",
        response_model=ListaGraficos | DadosGrafico,
        dependencies=protected,
        tags=["consultas"],
        operation_id="consultarGraficos",
        summary="Lista os gráficos de um host ou resume os dados de um gráfico",
        description=(
            "Sem grafico_id: lista os gráficos do host. Com grafico_id: devolve mínimo, média, "
            "máximo, último valor e até 24 pontos por série no período."
        ),
    )
    async def graficos(
        z: Zbx,
        host: Annotated[str | None, Query(description="Parte do nome do host")] = None,
        busca: Annotated[str | None, Query(description="Parte do nome do gráfico, ex.: CPU")] = None,
        grafico_id: Annotated[str | None, Query(description="ID do gráfico para trazer os dados")] = None,
        horas: Annotated[int, Query(ge=1, le=720, description="Período em horas (padrão 24)")] = 24,
        limite: Limite = None,
    ):
        if grafico_id:
            return await graph_data(z, grafico_id, horas)
        if not host:
            raise HTTPException(400, "Informe host (para listar gráficos) ou grafico_id (para os dados)")
        limit = limit_of(limite)
        params: dict[str, Any] = {
            "output": ["graphid", "name"],
            "hostids": await host_ids(z, host),
            "selectHosts": ["name"],
            "sortfield": "name",
            "limit": limit,
        }
        if busca:
            params["search"] = {"name": busca}
        graphs = await z.get("graph.get", params)
        return ListaGraficos(
            retornados=len(graphs),
            possivelmente_truncado=len(graphs) >= limit,
            graficos=[
                Grafico(
                    id=g["graphid"],
                    nome=g["name"],
                    hosts=[h["name"] for h in g.get("hosts", [])],
                    url=graph_url(g["graphid"], horas),
                )
                for g in graphs
            ],
        )

    async def graph_data(z: ZabbixClient, graphid: str, horas: int) -> DadosGrafico:
        graphs = await z.get(
            "graph.get",
            {
                "output": ["graphid", "name"],
                "graphids": [graphid],
                "selectItems": ["itemid", "name", "key_", "units", "value_type", "lastvalue"],
            },
        )
        if not graphs:
            raise HTTPException(404, f"Gráfico não encontrado: {graphid}")
        graph = graphs[0]
        numeric = [i for i in graph.get("items", []) if str(i.get("value_type")) in {"0", "3"}]
        time_from = int(time.time()) - horas * 3600

        trends: dict[str, list[dict[str, Any]]] = {}
        if numeric:
            rows = await z.get(
                "trend.get",
                {
                    "output": ["itemid", "clock", "value_min", "value_avg", "value_max"],
                    "itemids": [i["itemid"] for i in numeric],
                    "time_from": time_from,
                },
            )
            for row in rows:
                trends.setdefault(row["itemid"], []).append(row)

        series = []
        for item in numeric:
            rows = sorted(trends.get(item["itemid"], []), key=lambda r: int(r["clock"]))
            series.append(
                Serie(
                    item=item["name"],
                    chave=item["key_"],
                    unidade=item.get("units", ""),
                    minimo=min((float(r["value_min"]) for r in rows), default=None),
                    media=round(sum(float(r["value_avg"]) for r in rows) / len(rows), 4) if rows else None,
                    maximo=max((float(r["value_max"]) for r in rows), default=None),
                    ultimo_valor=item.get("lastvalue"),
                    pontos=_downsample(rows, 24),
                )
            )
        return DadosGrafico(id=graph["graphid"], nome=graph["name"], periodo_horas=horas, url=graph_url(graphid, horas), series=series)

    @app.get(
        "/hostgroups",
        response_model=ListaGrupos,
        dependencies=protected,
        tags=["consultas"],
        operation_id="listarGruposDeHosts",
        summary="Lista os grupos de hosts, para descobrir o nome a usar nos filtros",
    )
    async def hostgroups(
        z: Zbx,
        busca: Annotated[str | None, Query(description="Parte do nome do grupo")] = None,
        limite: Limite = None,
    ):
        limit = limit_of(limite)
        params: dict[str, Any] = {
            "output": ["groupid", "name"],
            "selectHosts": "count",
            "with_hosts": True,
            "sortfield": "name",
            "limit": limit,
        }
        if busca:
            params["search"] = {"name": busca}
        groups = await z.get("hostgroup.get", params)
        return ListaGrupos(
            retornados=len(groups),
            possivelmente_truncado=len(groups) >= limit,
            grupos=[GrupoHosts(id=g["groupid"], nome=g["name"], total_hosts=int(g.get("hosts", 0))) for g in groups],
        )

    @app.get(
        "/hosts-from-hostgroup",
        response_model=HostsDoGrupo,
        dependencies=protected,
        tags=["consultas"],
        operation_id="listarHostsDoGrupo",
        summary="Hosts de um grupo de hosts",
    )
    async def hosts_from_hostgroup(
        z: Zbx,
        grupo: Annotated[str, Query(description="Nome do grupo (exato ou parcial)")],
        tipo: Annotated[
            str | None, Query(pattern="^(vm|hypervisor|vcenter|outro)$", description="vm, hypervisor, vcenter ou outro")
        ] = None,
        limite: Limite = None,
    ):
        groups = await find_groups(z, grupo)
        if len(groups) > 1:
            nomes = ", ".join(g["name"] for g in groups[:10])
            raise HTTPException(409, f"Mais de um grupo corresponde a '{grupo}': {nomes}. Use o nome exato.")
        g = groups[0]
        limit = limit_of(limite)
        raw = await query_hosts(z, limit=limit, groupids=[g["groupid"]], tipo=tipo)
        return HostsDoGrupo(
            grupo=GrupoHosts(id=g["groupid"], nome=g["name"], total_hosts=int(g.get("hosts", 0))),
            retornados=len(raw),
            possivelmente_truncado=len(raw) >= limit,
            hosts=[to_host(h) for h in raw],
        )

    return app


def _downsample(rows: list[dict[str, Any]], max_points: int) -> list[Ponto]:
    if not rows:
        return []
    size = -(-len(rows) // max_points)  # teto da divisão
    points = []
    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]
        points.append(
            Ponto(
                inicio=_iso(chunk[0]["clock"]),
                media=round(sum(float(r["value_avg"]) for r in chunk) / len(chunk), 4),
            )
        )
    return points


app = create_app()
