import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEY = "chave-de-teste"
NOW = int(time.time())

TEMPLATES = [
    {"templateid": "10", "name": "VMware"},
    {"templateid": "11", "name": "VMware Guest"},
    {"templateid": "12", "name": "VMware Hypervisor"},
]
HOSTS = [
    {
        "hostid": "1", "host": "vm-app01", "name": "vm-app01", "status": "0", "maintenance_status": "0",
        "parentTemplates": [{"name": "VMware Guest"}], "interfaces": [],
        "hostgroups": [{"name": "VMs"}],
    },
    {
        "hostid": "2", "host": "esxi01", "name": "esxi01.local", "status": "0", "maintenance_status": "0",
        "parentTemplates": [{"name": "VMware Hypervisor"}], "interfaces": [],
        "hostgroups": [{"name": "Hypervisors"}],
    },
    {
        "hostid": "3", "host": "srv-db", "name": "srv-db", "status": "0", "maintenance_status": "1",
        "parentTemplates": [{"name": "Linux by Zabbix agent"}],
        "interfaces": [{"ip": "10.0.0.5", "dns": "", "available": "2"}],
        "hostgroups": [{"name": "Linux servers"}],
    },
]


class FakeZabbix:
    def __init__(self, version="7.0.5"):
        self.version = version
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        self.calls.append((method, params, request.headers.get("Authorization"), body.get("auth")))
        result = getattr(self, method.replace(".", "_"))(params)
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body["id"]})

    def apiinfo_version(self, _):
        return self.version

    def template_get(self, _):
        return TEMPLATES

    def hostgroup_get(self, p):
        groups = [{"groupid": "50", "name": "VMs", "hosts": "1"}, {"groupid": "51", "name": "VMs Produção", "hosts": "0"}]
        if "filter" in p:
            return [g for g in groups if g["name"] in p["filter"]["name"]]
        if "search" in p:
            return [g for g in groups if p["search"]["name"].lower() in g["name"].lower()]
        return groups

    def host_get(self, p):
        hosts = HOSTS
        if "templateids" in p:
            names = {t["name"] for t in TEMPLATES if t["templateid"] in p["templateids"]}
            hosts = [h for h in hosts if h["parentTemplates"][0]["name"] in names]
        if "hostids" in p:
            hosts = [h for h in hosts if h["hostid"] in p["hostids"]]
        if "groupids" in p:
            hosts = [h for h in hosts if h["hostgroups"][0]["name"] == "VMs"]
        if "search" in p:
            hosts = [h for h in hosts if p["search"]["host"] in h["host"]]
        return hosts[: p.get("limit", 1000)]

    def item_get(self, p):
        if p["search"]["key_"] == "vmware.vm.powerstate":
            return [{"hostid": "1", "key_": "vmware.vm.powerstate[{$VMWARE.URL},uuid]", "lastvalue": "0"}]
        return [{"hostid": "2", "key_": "vmware.hv.status[{$VMWARE.URL},esxi01]", "lastvalue": "1"}]

    def problem_get(self, p):
        problems = [
            {"eventid": "900", "objectid": "70", "name": "VM desligada", "severity": "5",
             "clock": str(NOW - 7200), "acknowledged": "0", "suppressed": "0"},
            {"eventid": "800", "objectid": "71", "name": "Disco quase cheio", "severity": "2",
             "clock": str(NOW - 60), "acknowledged": "1", "suppressed": "0"},
        ]
        return [x for x in problems if int(x["severity"]) in p["severities"]]

    def trigger_get(self, p):
        return {"70": {"triggerid": "70", "hosts": [{"name": "vm-app01"}]},
                "71": {"triggerid": "71", "hosts": [{"name": "srv-db"}]}}

    def graph_get(self, p):
        graph = {"graphid": "300", "name": "CPU utilization", "hosts": [{"name": "srv-db"}],
                 "items": [{"itemid": "400", "name": "CPU %", "key_": "system.cpu.util",
                            "units": "%", "value_type": "0", "lastvalue": "12.5"}]}
        return [graph]

    def trend_get(self, p):
        return [{"itemid": "400", "clock": str(NOW - 3600 * i), "value_min": "1", "value_avg": "10", "value_max": "50"}
                for i in range(48)]


@pytest.fixture
def fake():
    return FakeZabbix()


@pytest.fixture
def client(fake):
    settings = Settings(zabbix_url="https://zbx.exemplo/api_jsonrpc.php", zabbix_api_token="tok", connector_api_key=KEY)
    app = create_app(settings, transport=httpx.MockTransport(fake))
    with TestClient(app, headers={"X-API-Key": KEY}) as c:
        yield c


def test_requires_api_key(client):
    assert client.get("/hosts", headers={"X-API-Key": "errada"}).status_code == 401
    assert client.get("/health", headers={"X-API-Key": ""}).status_code == 200


def test_hosts_classifies_vmware(client, fake):
    data = client.get("/hosts").json()
    tipos = {h["host"]: h["tipo"] for h in data["hosts"]}
    assert tipos == {"vm-app01": "vm", "esxi01": "hypervisor", "srv-db": "outro"}
    srv = next(h for h in data["hosts"] if h["host"] == "srv-db")
    assert srv["disponibilidade"] == "indisponivel" and srv["em_manutencao"] and srv["ips"] == ["10.0.0.5"]
    # Token vai no cabeçalho na 7.x e nunca no apiinfo.version.
    assert fake.calls[0][0] == "apiinfo.version" and fake.calls[0][2] is None
    assert all(c[2] == "Bearer tok" for c in fake.calls[1:])


def test_hosts_filter_by_tipo(client):
    data = client.get("/hosts", params={"tipo": "vm"}).json()
    assert [h["host"] for h in data["hosts"]] == ["vm-app01"]


def test_token_in_body_on_old_zabbix():
    fake = FakeZabbix(version="6.0.20")
    settings = Settings(zabbix_url="https://z/api_jsonrpc.php", zabbix_api_token="tok", connector_api_key=KEY)
    with TestClient(create_app(settings, transport=httpx.MockTransport(fake)), headers={"X-API-Key": KEY}) as c:
        assert c.get("/hosts").status_code == 200
    method, params, header, auth = fake.calls[-1]
    assert method == "host.get" and header is None and auth == "tok"
    assert "selectGroups" in params


def test_incidentes(client):
    data = client.get("/incidentes").json()
    assert data["retornados"] == 2
    first = data["incidentes"][0]
    assert first["severidade"] == "Desastre" and first["hosts"] == ["vm-app01"] and first["duracao"].startswith("2h")


def test_alertas_criticos_only_high(client):
    data = client.get("/alertas-criticos").json()
    assert [i["nome"] for i in data["incidentes"]] == ["VM desligada"]


def test_disponibilidade(client):
    data = client.get("/disponibilidade").json()
    assert data["resumo"]["total"] == 3 and data["resumo"]["disponivel"] == 1
    by_name = {h["nome"]: h for h in data["hosts"]}
    assert set(by_name) == {"vm-app01", "srv-db"}
    assert by_name["vm-app01"]["detalhe"] == "VM desligada"
    all_hosts = client.get("/disponibilidade", params={"apenas_problemas": "false"}).json()["hosts"]
    assert {h["nome"]: h["disponibilidade"] for h in all_hosts}["esxi01.local"] == "disponivel"


def test_graficos_list_and_data(client):
    assert client.get("/graficos").status_code == 400
    lista = client.get("/graficos", params={"host": "srv"}).json()
    assert lista["graficos"][0]["url"] == "https://zbx.exemplo/chart2.php?graphid=300&from=now-24h&to=now"
    dados = client.get("/graficos", params={"grafico_id": "300", "horas": 48}).json()
    serie = dados["series"][0]
    assert serie["maximo"] == 50 and serie["media"] == 10 and len(serie["pontos"]) == 24


def test_hosts_from_hostgroup(client):
    data = client.get("/hosts-from-hostgroup", params={"grupo": "VMs"}).json()
    assert data["grupo"]["nome"] == "VMs" and [h["host"] for h in data["hosts"]] == ["vm-app01"]
    ambiguous = client.get("/hosts-from-hostgroup", params={"grupo": "vm"})
    assert ambiguous.status_code == 409


def test_limit_is_capped(client):
    assert client.get("/hosts", params={"limite": 10_000}).status_code == 422


def test_zabbix_error_returns_502():
    def broken(request):
        body = json.loads(request.content)
        if body["method"] == "apiinfo.version":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": "7.0.0", "id": body["id"]})
        return httpx.Response(200, json={"jsonrpc": "2.0", "error": {"message": "No permissions"}, "id": body["id"]})

    settings = Settings(zabbix_url="https://z/api_jsonrpc.php", zabbix_api_token="tok", connector_api_key=KEY)
    with TestClient(create_app(settings, transport=httpx.MockTransport(broken)), headers={"X-API-Key": KEY}) as c:
        r = c.get("/hosts")
    assert r.status_code == 502 and "No permissions" in r.json()["detail"]
