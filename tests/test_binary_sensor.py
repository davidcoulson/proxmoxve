# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""Tests for the Proxmox VE binary sensors."""

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.typing import UNDEFINED

from custom_components.proxmoxve.binary_sensor import (
    PROXMOX_BINARYSENSOR_VM,
    ProxmoxBinarySensorEntity,
    ProxmoxBinarySensorEntityDescription,
)
from custom_components.proxmoxve.button import PROXMOX_BUTTON_VM
from custom_components.proxmoxve.const import (
    ProxmoxCommand,
    ProxmoxKeyAPIParse,
    ProxmoxType,
)


def _locked_description() -> ProxmoxBinarySensorEntityDescription:
    """Return the 'Locked' binary sensor description."""
    return next(
        description
        for description in PROXMOX_BINARYSENSOR_VM
        if description.key == ProxmoxKeyAPIParse.LOCKED
    )


def _locked_binary_sensor(*, locked: bool) -> ProxmoxBinarySensorEntity:
    """Build a Locked binary sensor backed by a guest with the given lock state."""
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(locked=locked)
    return ProxmoxBinarySensorEntity(
        coordinator=coordinator,
        unique_id="test_locked",
        info_device={},
        description=_locked_description(),
    )


def test_locked_binary_sensor_is_on() -> None:
    """Test the Locked binary sensor reflects the guest lock state."""
    assert _locked_binary_sensor(locked=True).is_on is True
    assert _locked_binary_sensor(locked=False).is_on is False


def test_unlock_button_is_qemu_only() -> None:
    """Test the unlock button is registered for QEMU VMs only (LXC has no API unlock)."""
    unlock = next(
        description
        for description in PROXMOX_BUTTON_VM
        if description.key == ProxmoxCommand.UNLOCK
    )
    assert unlock.api_category is ProxmoxType.QEMU


@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("missing", [None, UNDEFINED])
def test_binary_reading_recovers_after_becoming_unknown(
    missing: object, *, inverted: bool
) -> None:
    """Unknown values stay unknown, including for inverted problem sensors."""
    entity = _locked_binary_sensor(locked=True)
    entity.entity_description = dataclasses.replace(
        _locked_description(), inverted=inverted
    )
    assert entity.is_on is (not inverted)
    entity.coordinator.data.locked = missing
    assert entity.is_on is None
    entity.coordinator.data.locked = False
    assert entity.is_on is inverted
    entity.coordinator.data = SimpleNamespace()
    assert entity.is_on is None
    entity.coordinator.data = None
    assert entity.is_on is None
    entity.coordinator.data = SimpleNamespace(locked=True)
    assert entity.is_on is (not inverted)
