# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""Test the Proxmox VE config flow."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import (
    SOURCE_USER,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxmoxve import DOMAIN
from custom_components.proxmoxve._vendor.aioproxmox.exceptions import ProxmoxAuthError
from custom_components.proxmoxve.const import (
    CONF_AUTO_DISCOVERY,
    CONF_DISKS_ENABLE,
    CONF_ENTITY_ID_PREFIX,
    CONF_ENTITY_ID_SCHEME,
    CONF_NODES,
    CONF_REALM,
    CONF_UPDATE_INTERVAL,
)

from .const import (
    MOCK_GET_RESPONSE,
    USER_INPUT_OK,
    USER_INPUT_PORT_TOO_BIG,
    USER_INPUT_PORT_TOO_SMALL,
    USER_INPUT_SELECTION,
    USER_INPUT_USER_HOST,
)
from .fake_api import api_error, ssl_rejection


async def test_flow_ok(hass: HomeAssistant) -> None:
    """Test flow ok."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "host"

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
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT_USER_HOST,
        )

        assert result["step_id"] == "expose"
        assert result["type"] == FlowResultType.FORM
        assert "flow_id" in result

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT_SELECTION,
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert "data" in result
        assert result["data"][CONF_HOST] == USER_INPUT_USER_HOST[CONF_HOST]


async def test_flow_accepts_a_realm_outside_the_pick_list(
    hass: HomeAssistant,
) -> None:
    """
    Test an LDAP or AD realm can still be typed in.

    The realm field offers pam and pve as a pick-list; anything else has to
    stay possible, and has to reach the entry as typed.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

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
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={**USER_INPUT_USER_HOST, CONF_REALM: "ldap"},
        )
        assert result["step_id"] == "expose"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=USER_INPUT_SELECTION,
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REALM] == "ldap"


async def test_flow_port_small(hass: HomeAssistant) -> None:
    """Test if port number too small."""
    with patch(
        "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxHTTPAuth._get_new_tokens",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT_PORT_TOO_SMALL,
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"][CONF_PORT] == "invalid_port"


async def test_flow_port_big(hass: HomeAssistant) -> None:
    """Test if port number too big."""
    with patch(
        "custom_components.proxmoxve._vendor.aioproxmox.ProxmoxHTTPAuth._get_new_tokens",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT_PORT_TOO_BIG,
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"][CONF_PORT] == "invalid_port"


async def test_flow_auth_error(hass: HomeAssistant) -> None:
    """Test errors in case username or password are incorrect."""
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=ProxmoxAuthError("mock msg"),
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT_OK
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"][CONF_USERNAME] == "auth_error"


async def test_flow_cant_connect(hass: HomeAssistant) -> None:
    """Test errors in case the connection fails."""
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=TimeoutError,
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT_OK
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"][CONF_HOST] == "cant_connect"


async def test_flow_ssl_error(hass: HomeAssistant) -> None:
    """Test errors in case the SSL certificare is not present or is not valid or is expired."""
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=ssl_rejection(),
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT_OK
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"][CONF_VERIFY_SSL] == "ssl_rejection"


async def test_flow_unknown_exception(hass: HomeAssistant) -> None:
    """Test errors in case of an unknown exception occurs."""
    with patch(
        "custom_components.proxmoxve.ProxmoxClient.build_client",
        side_effect=Exception,
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT_OK,
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "general_error"


async def test_flow_already_configured(hass: HomeAssistant) -> None:
    """Test flow in case entry already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT_OK,
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
            context={"source": SOURCE_USER},
            data=USER_INPUT_USER_HOST,
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_an_empty_selection_with_discovery_on_is_accepted(
    hass: HomeAssistant,
) -> None:
    """
    Test picking nothing and turning on discovery creates the entry.

    The node field was marked required in the form, so the form refused
    exactly the setup the switch is for - track everything, pick nothing.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

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
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT_USER_HOST
        )
        assert result["step_id"] == "expose"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_AUTO_DISCOVERY: True, CONF_ENTITY_ID_SCHEME: "extended"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NODES] == []
    assert result["options"][CONF_AUTO_DISCOVERY] is True
    assert result["options"][CONF_ENTITY_ID_SCHEME] == "extended"
    assert result["options"][CONF_ENTITY_ID_PREFIX] == "pve"
    # What the advanced options offer later starts at its default.
    assert result["options"][CONF_UPDATE_INTERVAL] == 60


async def test_an_empty_selection_without_discovery_asks_for_a_node(
    hass: HomeAssistant,
) -> None:
    """Test nothing picked and discovery off is an error at the node field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

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
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT_USER_HOST
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DISKS_ENABLE: False, CONF_ENTITY_ID_SCHEME: "standard"},
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "expose"
        assert result["errors"] == {CONF_NODES: "nodes_required"}

        # Picking a node on the re-shown form goes through.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT_SELECTION
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NODES] == ["pve"]


@pytest.mark.parametrize(
    ("error", "field", "message"),
    [
        (TimeoutError(), CONF_HOST, "cant_connect"),
        (ProxmoxAuthError("Code 401"), CONF_USERNAME, "auth_error"),
        (ssl_rejection(), CONF_VERIFY_SSL, "ssl_rejection"),
        (api_error(403, "Forbidden", "denied"), "base", "no_resources"),
        (api_error(401, "Unauthorized", "expired"), "base", "auth_error"),
    ],
)
async def test_resource_failure_after_login_keeps_host_form(
    hass: HomeAssistant, error: Exception, field: str, message: str
) -> None:
    """A successful login followed by a failed listing is a recoverable form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with (
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.build_client"),
        patch("custom_components.proxmoxve.config_flow.ProxmoxClient.get_api_client"),
        patch("custom_components.proxmoxve.config_flow.get_api", side_effect=error),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT_USER_HOST
        )
    assert result["step_id"] == "host"
    assert result["errors"] == {field: message}
    schema = result["data_schema"].schema
    host = next(key for key in schema if key.schema == CONF_HOST)
    assert host.description["suggested_value"] == USER_INPUT_USER_HOST[CONF_HOST]
