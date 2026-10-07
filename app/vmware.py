"""Regras para hosts monitorados pelos templates VMware do Zabbix (vSphere 8)."""

from typing import Any

# Tipos de host expostos pela API.
TIPO_VM = "vm"
TIPO_HYPERVISOR = "hypervisor"
TIPO_VCENTER = "vcenter"
TIPO_OUTRO = "outro"
TIPOS = (TIPO_VM, TIPO_HYPERVISOR, TIPO_VCENTER, TIPO_OUTRO)

# Itens dos templates oficiais "VMware Guest" e "VMware Hypervisor".
KEY_VM_POWERSTATE = "vmware.vm.powerstate"
KEY_HV_STATUS = "vmware.hv.status"

VM_POWERSTATE = {"0": "desligada", "1": "ligada", "2": "suspensa"}
HV_STATUS = {"0": "cinza", "1": "verde", "2": "amarelo", "3": "vermelho"}


def classify_template(name: str) -> str:
    """Tipo de host associado a um template, pelo nome do template oficial."""
    lowered = name.lower()
    if not lowered.startswith("vmware"):
        return TIPO_OUTRO
    if "guest" in lowered:
        return TIPO_VM
    if "hypervisor" in lowered:
        return TIPO_HYPERVISOR
    return TIPO_VCENTER


def classify_host(parent_templates: list[dict[str, Any]]) -> str:
    tipos = {classify_template(t.get("name", "")) for t in parent_templates}
    for tipo in (TIPO_VM, TIPO_HYPERVISOR, TIPO_VCENTER):
        if tipo in tipos:
            return tipo
    return TIPO_OUTRO


def availability_from_vmware(key: str, lastvalue: str) -> tuple[str, str]:
    """(disponibilidade, detalhe) a partir do último valor do item VMware."""
    if key.startswith(KEY_VM_POWERSTATE):
        estado = VM_POWERSTATE.get(lastvalue, "desconhecido")
        disponibilidade = {"ligada": "disponivel", "desligada": "indisponivel"}.get(estado, "desconhecido")
        return disponibilidade, f"VM {estado}"
    estado = HV_STATUS.get(lastvalue, "desconhecido")
    disponibilidade = {
        "verde": "disponivel",
        "amarelo": "degradado",
        "vermelho": "indisponivel",
    }.get(estado, "desconhecido")
    return disponibilidade, f"hypervisor {estado}"
