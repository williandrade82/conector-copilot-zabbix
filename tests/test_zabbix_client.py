import httpx
import pytest

from app.zabbix import ForbiddenMethodError, ZabbixClient, is_read_only


@pytest.mark.parametrize("method", ["host.get", "problem.get", "trend.get", "apiinfo.version"])
def test_read_methods_allowed(method):
    assert is_read_only(method)


@pytest.mark.parametrize(
    "method", ["host.create", "host.update", "host.delete", "event.acknowledge", "user.login", "script.execute"]
)
def test_write_methods_blocked(method):
    assert not is_read_only(method)


async def test_client_refuses_write_without_network():
    def fail(_):
        raise AssertionError("não deveria chamar o Zabbix")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        client = ZabbixClient("https://z/api_jsonrpc.php", "tok", http)
        with pytest.raises(ForbiddenMethodError):
            await client.get("host.delete", {})
