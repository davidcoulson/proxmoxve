# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""Tests for what the diagnostics download leaves out."""

from dataclasses import make_dataclass
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxmoxve.api import get_api, post_api
from custom_components.proxmoxve.const import (
    CONF_HA_ADMIN_PASSWORD,
    CONF_HA_ADMIN_REALM,
    CONF_HA_ADMIN_TOKEN_NAME,
    CONF_HA_ADMIN_USERNAME,
    COORDINATORS,
)
from custom_components.proxmoxve.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .fake_api import FakeProxmox

REDACTED = "**REDACTED**"


async def test_every_credential_is_redacted(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the optional cluster HA admin credentials never reach the download.

    Only the primary host, user and password were redacted; the HA admin
    password went out in clear text.
    """
    hass.config_entries.async_update_entry(
        current_entry,
        data={
            **current_entry.data,
            CONF_HA_ADMIN_USERNAME: "root",
            CONF_HA_ADMIN_TOKEN_NAME: "",
            CONF_HA_ADMIN_PASSWORD: "admin-secret",
            CONF_HA_ADMIN_REALM: "pam",
        },
    )
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, current_entry)

    config = diagnostics["config_entry"]
    assert config[CONF_HOST] == REDACTED
    assert config[CONF_USERNAME] == REDACTED
    assert config[CONF_PASSWORD] == REDACTED
    assert config[CONF_HA_ADMIN_USERNAME] == REDACTED
    assert config[CONF_HA_ADMIN_PASSWORD] == REDACTED
    assert "admin-secret" not in str(diagnostics)
    assert "secret" not in str(diagnostics["config_entry"])


async def test_cached_file_content_and_entity_state_are_redacted(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """File content, host names and addresses cannot escape through another route."""
    await hass.config_entries.async_setup(current_entry.entry_id)
    await hass.async_block_till_done()
    secret = "file-secret-canary"
    data_type = make_dataclass("Guest", ["guest_file_content", "node", "ip_address"])
    current_entry.runtime_data[COORDINATORS]["secret"] = AsyncMock(
        data=data_type(secret, "private-node.invalid", "2001:db8::99")
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=current_entry.entry_id, identifiers={("proxmoxve", "secret")}
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "proxmoxve",
        "test_guest_file_content",
        config_entry=current_entry,
        device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, secret[:8], {"guest_file_content": secret})
    diagnostics = await async_get_config_entry_diagnostics(hass, current_entry)
    for value in (
        secret,
        secret[:8],
        "private-node.invalid",
        "2001:db8::99",
        current_entry.data[CONF_HOST],
    ):
        assert value not in str(diagnostics)


async def test_api_logs_omit_payloads_and_query_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Debug logs do not contain response bodies, mutation data or file paths."""
    caplog.set_level("DEBUG", logger="custom_components.proxmoxve")
    api = AsyncMock()
    api.request.return_value = {
        "content": "file-secret-canary",
        "key": "subscription-canary",
    }
    await get_api(api, "file-read?file=private-path-canary")
    await post_api(api, "config", password="password-canary")  # noqa: S106
    for value in (
        "file-secret-canary",
        "subscription-canary",
        "private-path-canary",
        "password-canary",
    ):
        assert value not in caplog.text
