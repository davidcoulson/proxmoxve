# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""Test the Proxmox VE config flow."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import (
    SOURCE_IMPORT,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxmoxve import DOMAIN
from custom_components.proxmoxve._vendor.aioproxmox.exceptions import ProxmoxAuthError
from custom_components.proxmoxve.const import (
    CONF_CONTAINERS,
    CONF_LXC,
    CONF_NODE,
    CONF_NODES,
    CONF_QEMU,
    CONF_VMS,
)

from .const import (
    MOCK_GET_RESPONSE,
    USER_INPUT_USER_HOST,
    YAML_INPUT_NOT_EXIST,
    YAML_INPUT_OK,
)
from .fake_api import api_error, ssl_rejection


async def test_flow_import_ok(hass: HomeAssistant) -> None:
    """Test import flow ok."""
    conf = YAML_INPUT_OK[DOMAIN]
    with (
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxVE.request",
            return_value=MOCK_GET_RESPONSE,
        ),
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxHTTPAuth._get_new_tokens",
            return_value=None,
        ),
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=conf,
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert "data" in result
        assert result["data"][CONF_HOST] == conf[CONF_HOST]

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_success",
            )
            is not None
        )


async def test_flow_import_error_node_not_exist(hass: HomeAssistant) -> None:
    """Test import error in case node not exist in Proxmox."""
    conf = YAML_INPUT_NOT_EXIST[DOMAIN]
    with (
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxVE.request",
            return_value=MOCK_GET_RESPONSE,
        ),
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxHTTPAuth._get_new_tokens",
            return_value=None,
        ),
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        for nodes in conf.get(CONF_NODES):
            assert (
                issue_registry.async_get_issue(
                    DOMAIN,
                    f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_{nodes[CONF_NODE]}_import_node_not_exist",
                )
                is not None
            )


async def test_flow_import_error_auth_error(hass: HomeAssistant) -> None:
    """Test import errors in case username or password are incorrect."""
    conf = YAML_INPUT_OK[DOMAIN]
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=ProxmoxAuthError("mock msg"),
        return_value=None,
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_auth_error",
            )
            is not None
        )


async def test_flow_import_error_ssl_rejection(hass: HomeAssistant) -> None:
    """Test import errors in case the SSL certificare is not present or is not valid or is expired."""
    conf = YAML_INPUT_OK[DOMAIN]
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=ssl_rejection(),
        return_value=None,
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=USER_INPUT_USER_HOST
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_ssl_rejection",
            )
            is not None
        )


async def test_flow_import_error_cant_connect(hass: HomeAssistant) -> None:
    """Test import errors in case the connection fails."""
    conf = YAML_INPUT_OK[DOMAIN]
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=TimeoutError,
        return_value=None,
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_cant_connect",
            )
            is not None
        )


async def test_flow_import_error_general_error(hass: HomeAssistant) -> None:
    """Test import errors in case of an unknown exception occurs."""
    conf = YAML_INPUT_OK[DOMAIN]
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=Exception,
        return_value=None,
    ):
        # imported config is identical to the one generated from config flow
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_general_error",
            )
            is not None
        )


async def test_flow_import_error_already_configured(hass: HomeAssistant) -> None:
    """Test import error in case entry already configured."""
    conf = YAML_INPUT_OK[DOMAIN]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=conf,
    )

    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxVE.request",
            return_value=MOCK_GET_RESPONSE,
        ),
        patch(
            "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxHTTPAuth._get_new_tokens",
            return_value=None,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=conf,
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "import_failed"

        issue_registry = ir.async_get(hass)
        assert (
            issue_registry.async_get_issue(
                DOMAIN,
                f"{conf.get(CONF_HOST)}_{conf.get(CONF_PORT)}_import_already_configured",
            )
            is not None
        )


async def test_import_accumulates_guests_from_every_node(hass: HomeAssistant) -> None:
    """Every selected guest survives a multi-node import, once and as a string."""
    conf = {
        **YAML_INPUT_OK[DOMAIN],
        CONF_NODES: [
            {CONF_NODE: "node-a", CONF_VMS: [101, "101"], CONF_CONTAINERS: [100]},
            {CONF_NODE: "node-b", CONF_VMS: [102], CONF_CONTAINERS: [103]},
        ],
    }
    with (
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.build_client"),
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.get_api_client"),
        patch(
            "custom_components.proxmoxve.config_flow.get_api",
            return_value=[{CONF_NODE: "node-a"}, {CONF_NODE: "node-b"}],
        ),
        patch("custom_components.proxmoxve.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=conf,
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NODES] == ["node-a", "node-b"]
    assert result["data"][CONF_QEMU] == ["101", "102"]
    assert result["data"][CONF_LXC] == ["100", "103"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "import_failed"),
        (api_error(403, "Forbidden", "denied"), "no_resources"),
        (api_error(401, "Unauthorized", "expired"), "auth_error"),
    ],
)
async def test_import_listing_failure_after_login(
    hass: HomeAssistant, error: Exception, reason: str
) -> None:
    """The node listing is guarded even after authentication succeeds."""
    with (
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.build_client"),
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.get_api_client"),
        patch("custom_components.proxmoxve.config_flow.get_api", side_effect=error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=YAML_INPUT_OK[DOMAIN]
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == reason
