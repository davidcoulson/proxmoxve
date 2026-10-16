# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""Tests for moving to another node of the cluster when the configured one is gone."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.proxmoxve._vendor.aioproxmox import ProxmoxVE
from custom_components.proxmoxve.api import ProxmoxClient
from custom_components.proxmoxve.const import COORDINATORS, PROXMOX_CLIENT, ProxmoxType

from .fake_api import NODE, FakeProxmox, api_error, connection_refused

CONFIGURED = "192.168.10.101"
LEARNED = ("192.0.2.10", "192.0.2.11")


async def test_learned_hosts_come_after_the_configured_one(
    hass: HomeAssistant, fake_api: FakeProxmox
) -> None:
    """Test the configured host stays first and repeats are dropped."""
    client = ProxmoxClient(
        hass,
        host=CONFIGURED,
        user="homeassistant",
        password="secret",  # noqa: S106 - invented
        token_name="homeassistant",  # noqa: S106 - not a secret
        realm="pve",
        verify_ssl=False,
    )
    await client.build_client()

    client.learn_hosts([*LEARNED, CONFIGURED, LEARNED[0], "", None])  # type: ignore[list-item]

    assert client.hosts == (CONFIGURED, *LEARNED)
    assert client.host == CONFIGURED


async def test_the_cluster_stays_in_home_assistant_when_the_host_goes(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the reported problem: one host down took the whole cluster out.

    Everything goes through the configured host's pveproxy. Once it is
    gone, the API object moves to a node it learned from `cluster/status`
    and the coordinators keep reading - they all hold that one object, so
    the move reaches every one of them.
    """
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    assert current_entry.state is ConfigEntryState.LOADED
    client: ProxmoxClient = current_entry.runtime_data[PROXMOX_CLIENT]
    assert client.hosts == (CONFIGURED, "192.0.2.10", "192.0.2.11")
    node = current_entry.runtime_data[COORDINATORS][f"{ProxmoxType.Node}_{NODE}"]
    assert node.proxmox is client.get_api_client()

    fake_api.dead_hosts.add(CONFIGURED)
    seen_before = len(fake_api.hosts_seen)
    await node.async_refresh()

    assert node.last_update_success
    assert client.host == "192.0.2.10"
    # The dead host was tried once for this poll, then the fallback answered
    # - that read and every one after it.
    assert fake_api.hosts_seen[seen_before] == CONFIGURED
    assert set(fake_api.hosts_seen[seen_before + 1 :]) == {"192.0.2.10"}


async def test_a_second_coordinator_finds_the_switch_done(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test the next poll after a switch goes to the fallback straight away."""
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    coordinators = current_entry.runtime_data[COORDINATORS]
    node = coordinators[f"{ProxmoxType.Node}_{NODE}"]
    lxc = coordinators[f"{ProxmoxType.LXC}_100"]

    fake_api.dead_hosts.add(CONFIGURED)
    await node.async_refresh()
    seen_before = len(fake_api.hosts_seen)
    await lxc.async_refresh()

    assert lxc.last_update_success
    assert set(fake_api.hosts_seen[seen_before:]) == {"192.0.2.10"}


async def test_without_any_other_node_the_failure_is_reported(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test a single node that goes away still fails the update, as before."""
    del fake_api.routes["cluster/status"]
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    client: ProxmoxClient = current_entry.runtime_data[PROXMOX_CLIENT]
    assert client.hosts == (CONFIGURED,)
    node = current_entry.runtime_data[COORDINATORS][f"{ProxmoxType.Node}_{NODE}"]

    fake_api.dead_hosts.add(CONFIGURED)
    await node.async_refresh()

    assert not node.last_update_success
    assert client.host == CONFIGURED


async def test_nothing_else_answering_leaves_the_configured_host(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test the original failure stands when no other node answers either."""
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    client: ProxmoxClient = current_entry.runtime_data[PROXMOX_CLIENT]
    node = current_entry.runtime_data[COORDINATORS][f"{ProxmoxType.Node}_{NODE}"]

    fake_api.dead_hosts.update((CONFIGURED, *LEARNED))
    await node.async_refresh()

    assert not node.last_update_success
    assert client.host == CONFIGURED


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_ambiguous_command_failure_is_not_replayed(
    hass: HomeAssistant, method: str
) -> None:
    """A lost response must never execute a command twice."""
    client = ProxmoxClient(
        hass,
        host=CONFIGURED,
        user="ha",
        password="secret",  # noqa: S106
        token_name="ha",  # noqa: S106
        realm="pve",
        verify_ssl=False,
    )
    with patch(
        "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxVE._request_once",
        new=AsyncMock(return_value={}),
    ):
        await client.build_client()
    proxmox = client.get_api_client()
    proxmox.learn_hosts(list(LEARNED))
    with (
        patch.object(proxmox, "_request_once", side_effect=TimeoutError) as request,
        patch.object(proxmox, "failover", return_value=True) as failover,
        pytest.raises(TimeoutError),
    ):
        await proxmox.request(method, "nodes/pve/vzdump")
    request.assert_awaited_once()
    failover.assert_not_awaited()


@pytest.mark.parametrize(
    ("host", "port", "expected_host", "expected_port"),
    [
        ("192.0.2.1", None, "192.0.2.1", 8006),
        ("node.invalid:8443", None, "node.invalid", 8443),
        ("node.invalid:8443", 9000, "node.invalid", 9000),
        ("2001:db8::10", None, "2001:db8::10", 8006),
        ("[2001:db8::10]", None, "2001:db8::10", 8006),
        ("[2001:db8::10]:8443", None, "2001:db8::10", 8443),
    ],
)
def test_host_and_port_parsing(
    host: str, port: int | None, expected_host: str, expected_port: int
) -> None:
    """Configured and learned endpoints preserve IPv6 and explicit ports."""
    api = ProxmoxVE(
        MagicMock(),
        host,
        port=port,
        user="ha",
        token_name="ha",  # noqa: S106
        token_value="fake",  # noqa: S106
    )
    assert URL(api.base_url).host == expected_host
    assert URL(api.base_url).port == expected_port
    api.learn_hosts(
        ["2001:db8::11", "[2001:db8::11]", "[2001:db8::12]:9443", "bad.invalid/path"]
    )
    assert api.hosts[1:] == ("2001:db8::11", "[2001:db8::12]:9443")
    api._use_host(1)  # noqa: SLF001
    assert URL(api.base_url).host == "2001:db8::11"
    assert URL(api.base_url).port == expected_port
    api._use_host(2)  # noqa: SLF001
    assert URL(api.base_url).host == "2001:db8::12"
    assert URL(api.base_url).port == 9443
    assert api.auth.base_url == api.base_url


async def test_simultaneous_failures_reuse_validated_host() -> None:
    """Both requests failed on A must recover on B without publishing a probe."""
    api = ProxmoxVE(
        MagicMock(),
        CONFIGURED,
        user="ha",
        token_name="ha",  # noqa: S106
        token_value="fake",  # noqa: S106
    )
    api.learn_hosts([LEARNED[0]])
    barrier = asyncio.Barrier(2)

    async def request(client: ProxmoxVE, method: str, path: str, *args: object) -> dict:
        if client.host == CONFIGURED:
            await barrier.wait()
            raise connection_refused()
        if path == "version":
            assert api.host == CONFIGURED
            await asyncio.sleep(0)
        return {"ok": True}

    with patch.object(ProxmoxVE, "_request_once", autospec=True, side_effect=request):
        results = await asyncio.gather(
            api.request("GET", "nodes"), api.request("GET", "nodes")
        )
    assert results == [{"ok": True}, {"ok": True}]
    assert api.host == LEARNED[0]


async def test_expired_ticket_authenticates_on_candidate() -> None:
    """A dead ticket-renewal host must not prevent login on a live candidate."""
    api = ProxmoxVE(MagicMock(), CONFIGURED, user="ha", password="fake")  # noqa: S106
    api.learn_hosts([LEARNED[0]])
    api.auth.pve_auth_ticket = "expired"

    async def refresh(auth, method: str) -> None:
        if URL(auth.base_url).host == CONFIGURED:
            raise connection_refused()

    async def login(auth) -> None:
        assert URL(auth.base_url).host == LEARNED[0]
        assert api.host == CONFIGURED
        assert api.auth.pve_auth_ticket == "expired"
        auth.pve_auth_ticket = "fresh"

    async def request(client: ProxmoxVE, method: str, path: str, *args: object) -> dict:
        if client.auth.pve_auth_ticket == "expired":
            raise api_error(401, "Unauthorized", "expired")
        return {"ok": True}

    with (
        patch.object(type(api.auth), "check_and_refresh", refresh),
        patch.object(type(api.auth), "relogin", login),
        patch.object(ProxmoxVE, "_request_once", autospec=True, side_effect=request),
    ):
        assert await api.request("GET", "nodes") == {"ok": True}
    assert api.host == LEARNED[0]
    assert api.auth.pve_auth_ticket == "fresh"


async def test_optional_client_shares_discovered_hosts(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Both credential sets recover and receive endpoints discovered later."""
    from custom_components.proxmoxve.const import (  # noqa: PLC0415
        CONF_HA_ADMIN_PASSWORD,
        CONF_HA_ADMIN_USERNAME,
        PROXMOX_HA_ADMIN_CLIENT,
    )

    hass.config_entries.async_update_entry(
        current_entry,
        data={
            **current_entry.data,
            CONF_HA_ADMIN_USERNAME: "admin",
            CONF_HA_ADMIN_PASSWORD: "fake",
        },
    )
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    primary = current_entry.runtime_data[PROXMOX_CLIENT]
    admin = current_entry.runtime_data[PROXMOX_HA_ADMIN_CLIENT]
    assert admin.hosts == primary.hosts
    primary.learn_hosts(["192.0.2.99"])
    assert admin.hosts == primary.hosts
    fake_api.dead_hosts.add(CONFIGURED)
    for client in (primary, admin):
        await client.get_api_client().request("GET", "version")
        assert client.host == LEARNED[0]
