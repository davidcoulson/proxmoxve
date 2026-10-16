# Copyright (c) 2019-2026
# SPDX-License-Identifier: MIT
"""
Setting up against a Proxmox that answers every path.

These are the first tests that run setup with a real node name. The older
setup tests go through the version-1 migration, which loses the node, so
the node coordinators, disks, pools, buttons and the update entity were
never actually exercised there.
"""

from dataclasses import replace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proxmoxve import DOMAIN
from custom_components.proxmoxve.const import (
    CONF_LXC,
    CONF_NODES,
    CONF_QEMU,
    CONF_STORAGE,
    CONF_UPDATES_ENABLE,
    COORDINATORS,
    ProxmoxType,
)

from .fake_api import NODE, FakeProxmox, api_error, lxc_status
from .test_replication import HEALTHY


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up and wait for the platforms."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _state(hass: HomeAssistant, entry: MockConfigEntry, unique_id: str, domain: str):  # noqa: ANN202
    """Return the state of the entity with the given unique id."""
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, f"no {domain} entity with unique id {unique_id}"
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state (disabled?)"
    return state


async def test_every_coordinator_reads_its_data(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test each coordinator ends its first refresh with data, not an error."""
    await _setup(hass, current_entry)

    coordinators = current_entry.runtime_data[COORDINATORS]
    for key, coordinator in coordinators.items():
        for single in coordinator if isinstance(coordinator, list) else [coordinator]:
            assert single.last_update_success, f"{key} failed its first refresh"
            assert single.data is not None, f"{key} has no data"

    assert set(coordinators) >= {
        "node_pve",
        "update_pve",
        "certificate_pve",
        "subscription_pve",
        "replication_pve",
        "backup_pve",
        "tasks_pve",
        "disk_pve",
        "zfs_pve",
        "qemu_101",
        "lxc_100",
        "storage_storage/pve/local",
    }
    assert len(coordinators["disk_pve"]) == 1
    assert len(coordinators["zfs_pve"]) == 1


async def test_the_node_and_its_guests_report(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test a few states the fixtures pin down, across the platforms."""
    await _setup(hass, current_entry)
    entry_id = current_entry.entry_id

    assert _state(hass, current_entry, f"{entry_id}_pve_cpu", "sensor").state == "1.35"
    assert _state(
        hass, current_entry, f"{entry_id}_pve_status", "binary_sensor"
    ).state == (STATE_ON)
    assert _state(
        hass, current_entry, f"{entry_id}_pve_status_raw", "sensor"
    ).state == ("online")
    assert _state(
        hass, current_entry, f"{entry_id}_101_status_raw", "sensor"
    ).state == ("running")
    assert _state(
        hass, current_entry, f"{entry_id}_100_status_raw", "sensor"
    ).state == ("running")
    # The VM's disk usage comes from the guest agent, not the host's zero.
    disk = _state(hass, current_entry, f"{entry_id}_101_disk_used_perc", "sensor")
    assert 36 < float(disk.state) < 37
    # The storage flags come from the node's own storage list.
    active = _state(
        hass, current_entry, f"{entry_id}_storage/pve/local_active", "binary_sensor"
    )
    assert active.state == STATE_ON


async def test_the_update_entity_reads_like_the_core_integration(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test installed and latest version come from the node and apt/update."""
    await _setup(hass, current_entry)

    state = _state(
        hass, current_entry, f"{current_entry.entry_id}_pve_node_update", "update"
    )
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "9.0.6"
    assert state.attributes["latest_version"] == "9.0.10-p1-d1"


async def test_buttons_follow_the_permissions(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test a token without VM.Snapshot gets no snapshot button, and keeps the rest."""
    fake_api.routes["access/permissions"] = {
        "/": {"Sys.Audit": 1, "VM.Audit": 1, "Datastore.Audit": 1, "Sys.Modify": 1},
        "/vms/101": {"VM.PowerMgmt": 1},
    }
    await _setup(hass, current_entry)
    registry = er.async_get(hass)
    entry_id = current_entry.entry_id

    assert registry.async_get_entity_id("button", DOMAIN, f"{entry_id}_101_start")
    assert not registry.async_get_entity_id(
        "button", DOMAIN, f"{entry_id}_101_snapshot"
    )
    # Nothing was granted on the container at all.
    assert not registry.async_get_entity_id("button", DOMAIN, f"{entry_id}_100_start")
    # Sys.PowerMgmt on the node is missing, VM.PowerMgmt on a guest is there.
    assert not registry.async_get_entity_id("button", DOMAIN, f"{entry_id}_pve_reboot")
    assert registry.async_get_entity_id("button", DOMAIN, f"{entry_id}_pve_startall")


async def test_no_sys_modify_means_no_update_entity_and_no_repair(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test a read-only token gets no package-update entity, and no repair.

    Reading `apt/update` needs Sys.Modify, a management privilege a
    read-only setup deliberately does not hold. The coordinator used to be
    created anyway, get a 403, and raise a repair demanding the permission;
    the update entity was skipped but the count sensor still appeared with
    nothing in it.
    """
    fake_api.routes["access/permissions"] = {
        "/": {"Sys.Audit": 1, "VM.Audit": 1, "Datastore.Audit": 1},
    }
    fake_api.routes[f"nodes/{NODE}/apt/update"] = api_error(
        403, "Permission check failed", "(/nodes/pve, [Sys.Modify])"
    )

    await _setup(hass, current_entry)

    coordinators = current_entry.runtime_data[COORDINATORS]
    assert f"{ProxmoxType.Update}_{NODE}" not in coordinators
    registry = er.async_get(hass)
    entry_id = current_entry.entry_id
    assert not registry.async_get_entity_id(
        "update", DOMAIN, f"{entry_id}_pve_node_update"
    )
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"{entry_id}_forbidden")


async def test_updates_can_be_switched_off_whatever_the_privileges(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the option leaves package updates out even with Sys.Modify held.

    Upstream #588 asked for exactly this: a setup that does not want the
    entities, or does not want to grant the privilege, opts out instead of
    being told what is missing.
    """
    hass.config_entries.async_update_entry(
        current_entry, options={**current_entry.options, CONF_UPDATES_ENABLE: False}
    )

    await _setup(hass, current_entry)

    coordinators = current_entry.runtime_data[COORDINATORS]
    assert f"{ProxmoxType.Update}_{NODE}" not in coordinators
    entry_id = current_entry.entry_id
    assert not er.async_get(hass).async_get_entity_id(
        "update", DOMAIN, f"{entry_id}_pve_node_update"
    )
    assert f"nodes/{NODE}/apt/update" not in fake_api.paths()


async def test_unknown_permissions_still_create_the_update_entity(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test not being able to read the privileges gates nothing."""
    fake_api.routes["access/permissions"] = api_error(
        403, "Permission check failed", ""
    )

    await _setup(hass, current_entry)

    assert f"{ProxmoxType.Update}_{NODE}" in current_entry.runtime_data[COORDINATORS]


async def test_a_stopped_container_has_no_disk_usage_to_report(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test a container that is off reads unknown for disk, and 0 for memory.

    Proxmox reports `disk: 0` for a stopped container because it cannot
    look inside - the data is still on the volume. That 0 became 0% used
    and 100% free on every stop, a spike in every graph. Memory really is
    zero then, so that one stays a measurement.
    """
    fake_api.routes[f"nodes/{NODE}/lxc/100/status/current"] = lxc_status(
        100, "lxc-test-100", status="stopped"
    )

    await _setup(hass, current_entry)

    entry_id = current_entry.entry_id
    disk_used = _state(hass, current_entry, f"{entry_id}_100_disk_used_perc", "sensor")
    assert disk_used.state == "unknown"
    memory = _state(hass, current_entry, f"{entry_id}_100_memory_used_perc", "sensor")
    assert memory.state == "0"


async def test_the_node_reports_io_delay_and_its_version(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the two node figures people kept asking for are entities now.

    `wait` is the share of time the CPUs spent waiting for I/O, on the same
    0..1 scale as `cpu`; Proxmox shows it as IO delay. The version was only
    ever visible on the device page.
    """
    await _setup(hass, current_entry)

    entry_id = current_entry.entry_id
    io_delay = _state(hass, current_entry, f"{entry_id}_pve_io_wait", "sensor")
    assert float(io_delay.state) == 0.42
    assert io_delay.attributes["unit_of_measurement"] == "%"
    version = _state(hass, current_entry, f"{entry_id}_pve_version", "sensor")
    assert version.state == "9.0.6"


async def test_the_node_device_carries_its_mac_addresses(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the node device is linked by the hardware addresses of its ports.

    `nodes/{node}/network` carries no MAC field, but systemd's MAC-based
    interface name sits in `altnames`, and that is enough for Home
    Assistant to merge the node with what a network integration sees.
    """
    await _setup(hass, current_entry)

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{current_entry.entry_id}_NODE_{NODE}"), current_entry.entry_id
    )
    assert device is not None
    assert device.connections == {
        (dr.CONNECTION_NETWORK_MAC, "02:00:0a:0b:0c:0d"),
        (dr.CONNECTION_NETWORK_MAC, "02:00:0a:0b:0c:0e"),
    }
    # Read once, not on every poll.
    assert sum(1 for call in fake_api.calls if call[1].endswith("/network")) == 1


async def test_disks_and_pools_get_their_own_devices(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Test the disk and the pool hang off the node's device."""
    await _setup(hass, current_entry)
    dev_reg = dr.async_get(hass)
    entry_id = current_entry.entry_id

    node = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_NODE_pve"), entry_id
    )
    disk = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_DISK_pve_0x5002538e40000001"), entry_id
    )
    # The pool device carries the data's display name, not the pool name.
    pool = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_ZFS_pve_ZFS Pool rpool"), entry_id
    )

    assert node is not None
    assert disk is not None
    assert pool is not None
    assert disk.via_device_id == node.id
    assert pool.via_device_id == node.id
    assert disk.serial_number == "S123"

    temperature = _state(
        hass, current_entry, f"{entry_id}_pve_0x5002538e40000001_temperature", "sensor"
    )
    assert temperature.state == "34"
    health = _state(
        hass, current_entry, f"{entry_id}_pve_ZFS Pool rpool_health", "sensor"
    )
    assert health.state == "ONLINE"


async def test_only_the_expected_paths_are_read(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test setup does not reach for anything the fake does not know.

    The fake refuses unknown paths with a 404, which a coordinator would
    report as a failed refresh; this makes the list of paths visible when
    a new coordinator forgets to add its route.
    """
    await _setup(hass, current_entry)

    unknown = sorted({path for path in fake_api.paths() if path not in fake_api.routes})
    assert unknown == []
    assert fake_api.paths("POST") == []


async def test_deselecting_the_node_removes_its_disk_and_pool_devices(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """
    Test the options flow can drop a node that owns disks and pools.

    This used to raise AttributeError: the pool device was looked up under a
    `path` attribute that pool data does not have.
    """
    await _setup(hass, current_entry)
    dev_reg = dr.async_get(hass)
    entry_id = current_entry.entry_id
    pool = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_ZFS_pve_ZFS Pool rpool"), entry_id
    )
    disk = dev_reg.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_DISK_pve_0x5002538e40000001"), entry_id
    )
    assert pool is not None
    assert disk is not None

    result = await hass.config_entries.options.async_init(entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "change_expose"}
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_NODES: [],
            CONF_QEMU: ["101"],
            CONF_LXC: ["100"],
            CONF_STORAGE: [],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "changes_successful"
    assert dev_reg.async_get(pool.id) is None
    assert dev_reg.async_get(disk.id) is None


async def test_guest_entities_recover_after_failed_first_read(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Unavailable guests already have entities and listeners that keep polling."""
    path = f"nodes/{NODE}/qemu/101/status/current"
    healthy = fake_api.routes[path]
    fake_api.routes[path] = api_error(500, "Internal Server Error", "starting")
    await _setup(hass, current_entry)
    coordinator = current_entry.runtime_data[COORDINATORS]["qemu_101"]
    assert coordinator.data is None
    assert er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{current_entry.entry_id}_101_shutdown"
    )
    state = _state(
        hass, current_entry, f"{current_entry.entry_id}_101_status_raw", "sensor"
    )
    assert state.state == "unavailable"
    fake_api.routes[path] = healthy
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (
        _state(
            hass, current_entry, f"{current_entry.entry_id}_101_status_raw", "sensor"
        ).state
        == "running"
    )


async def test_new_response_keys_add_entities_once(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """Cold hardware, replication and the first backup appear without a reload."""
    backup_path = f"nodes/{NODE}/tasks?typefilter=vzdump&source=archive&limit=1"
    backup = fake_api.routes[backup_path]
    fake_api.routes[backup_path] = []
    await _setup(hass, current_entry)
    coordinators = current_entry.runtime_data[COORDINATORS]
    registry = er.async_get(hass)
    before = set(registry.entities)
    node = coordinators["node_pve"]
    node.async_set_updated_data(
        replace(node.data, sensors={"coretemp-isa-0000 Package id 0": 57.0})
    )
    fake_api.routes[f"nodes/{NODE}/replication"] = [HEALTHY]
    fake_api.routes[backup_path] = backup
    await coordinators["replication_pve"].async_refresh()
    await coordinators["backup_pve"].async_refresh()
    await hass.async_block_till_done()
    added = [
        registry.entities[key].unique_id for key in set(registry.entities) - before
    ]
    assert any("coretemp" in key for key in added)
    assert any("replication_pve" in key for key in added)
    assert any("backup_pve" in key for key in added)
    count = len(registry.entities)
    node.async_update_listeners()
    coordinators["replication_pve"].async_update_listeners()
    coordinators["backup_pve"].async_update_listeners()
    await hass.async_block_till_done()
    assert len(registry.entities) == count


async def test_stopped_container_and_unreadable_vm_fields_recover(
    hass: HomeAssistant, fake_api: FakeProxmox, current_entry: MockConfigEntry
) -> None:
    """IP and disk entities exist before a stopped or unreadable guest reports."""
    lxc_path = f"nodes/{NODE}/lxc/100/status/current"
    healthy_lxc = fake_api.routes[lxc_path]
    fake_api.routes[lxc_path] = lxc_status(100, "lxc-test-100", "stopped")
    disk_path = f"nodes/{NODE}/qemu/101/agent/get-fsinfo"
    healthy_disk = fake_api.routes[disk_path]
    fake_api.routes[disk_path] = api_error(
        500, "Internal Server Error", "agent starting"
    )
    await _setup(hass, current_entry)
    registry = er.async_get(hass)
    for suffix in ("100_ip_address", "101_disk_used"):
        assert registry.async_get_entity_id(
            "sensor", DOMAIN, f"{current_entry.entry_id}_{suffix}"
        )
    coordinators = current_entry.runtime_data[COORDINATORS]
    fake_api.routes[lxc_path] = healthy_lxc
    fake_api.routes[disk_path] = healthy_disk
    await coordinators["lxc_100"].async_refresh()
    await coordinators["qemu_101"].async_refresh()
    await hass.async_block_till_done()
    assert (
        _state(
            hass, current_entry, f"{current_entry.entry_id}_100_ip_address", "sensor"
        ).state
        == "192.0.2.20"
    )
    assert coordinators["qemu_101"].data.disk_used > 0
