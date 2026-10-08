"""Modelos de resposta. Campos curtos e resumidos para caber no contexto do Copilot."""

from pydantic import BaseModel, Field


class Host(BaseModel):
    id: str
    host: str = Field(description="Nome técnico do host no Zabbix")
    nome: str = Field(description="Nome visível do host")
    status: str = Field(description="ativo ou desativado")
    em_manutencao: bool
    tipo: str = Field(description="vm, hypervisor, vcenter ou outro (pelos templates VMware)")
    grupos: list[str]
    ips: list[str]
    disponibilidade: str = Field(
        description="disponivel, indisponivel ou desconhecido, pelas interfaces do host. "
        "Hosts VMware não têm interface; use /disponibilidade para o estado deles."
    )


class ListaHosts(BaseModel):
    retornados: int
    possivelmente_truncado: bool = Field(description="true se atingiu o limite; refine os filtros")
    hosts: list[Host]


class Incidente(BaseModel):
    id: str = Field(description="eventid do problema")
    nome: str
    severidade: str
    severidade_nivel: int = Field(description="0 a 5 (5 = Desastre)")
    hosts: list[str]
    inicio: str = Field(description="Data/hora de início em ISO 8601 (UTC)")
    duracao: str = Field(description="Duração legível, ex.: 2d 3h 10m")
    reconhecido: bool
    suprimido: bool


class ListaIncidentes(BaseModel):
    retornados: int
    possivelmente_truncado: bool
    por_severidade: dict[str, int]
    incidentes: list[Incidente]


class HostDisponibilidade(BaseModel):
    id: str
    nome: str
    tipo: str
    disponibilidade: str = Field(description="disponivel, indisponivel, degradado ou desconhecido")
    detalhe: str


class ResumoDisponibilidade(BaseModel):
    total: int
    disponivel: int
    indisponivel: int
    degradado: int
    desconhecido: int
    percentual_disponivel: float


class Disponibilidade(BaseModel):
    resumo: ResumoDisponibilidade
    por_tipo: dict[str, ResumoDisponibilidade]
    hosts_analisados_truncado: bool = Field(description="true se havia mais hosts que o teto de análise")
    hosts: list[HostDisponibilidade] = Field(
        description="Hosts não disponíveis, ou todos os filtrados se apenas_problemas=false"
    )


class Grafico(BaseModel):
    id: str
    nome: str
    hosts: list[str]
    url: str = Field(description="Link do gráfico na interface do Zabbix (exige login)")


class ListaGraficos(BaseModel):
    retornados: int
    possivelmente_truncado: bool
    graficos: list[Grafico]


class Ponto(BaseModel):
    inicio: str
    media: float


class Serie(BaseModel):
    item: str
    chave: str
    unidade: str
    minimo: float | None
    media: float | None
    maximo: float | None
    ultimo_valor: str | None
    pontos: list[Ponto] = Field(description="Médias por intervalo, no máximo 24 pontos")


class DadosGrafico(BaseModel):
    id: str
    nome: str
    periodo_horas: int
    url: str
    series: list[Serie]


class GrupoHosts(BaseModel):
    id: str
    nome: str
    total_hosts: int


class ListaGrupos(BaseModel):
    retornados: int
    possivelmente_truncado: bool
    grupos: list[GrupoHosts]


class HostsDoGrupo(BaseModel):
    grupo: GrupoHosts
    retornados: int
    possivelmente_truncado: bool
    hosts: list[Host]
