# Copyright 2020 Northern.tech AS
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        https://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import pytest
import time

from datetime import datetime, timedelta, timezone

from testutils.util.artifact import Artifact
from testutils.infra.container_manager import factory
from testutils.infra.device import MenderDevice
from testutils.common import create_org, create_user
from testutils.api.client import ApiClient
from testutils.api import deviceauth, useradm, inventory, deployments

from .. import conftest

container_factory = factory.get_factory()

TIMEOUT = timedelta(minutes=5)


@pytest.fixture(scope="function")
def setup_os_compat(request):
    env = container_factory.getCompatibilitySetup()
    request.addfinalizer(env.teardown)
    env.setup()

    env.user = create_user(
        "test@mender.io", "correcthorse", containers_namespace=env.name
    )
    env.populate_clients()

    clients = env.get_mender_clients()
    assert len(clients) > 0, "Failed to setup clients"
    env.devices = []
    for client in clients:
        dev = MenderDevice(client)
        dev.ssh_is_opened()
        env.devices.append(dev)

    return env


@pytest.fixture(scope="function")
def setup_ent_compat(request):
    env = container_factory.getCompatibilitySetup(enterprise=True)
    request.addfinalizer(env.teardown)
    env.setup()

    env.tenant = create_org(
        "Mender",
        "test@mender.io",
        "correcthorse",
        containers_namespace=env.name,
        container_manager=env,
    )
    env.user = env.tenant.users[0]

    env.populate_clients(tenant_token=env.tenant.tenant_token)

    clients = env.get_mender_clients()
    assert len(clients) > 0, "Failed to setup clients"
    env.devices = []
    for client in clients:
        dev = MenderDevice(client)
        dev.ssh_is_opened()
        env.devices.append(dev)

    return env


def accept_devices(api_deviceauth, devices=None):
    """
    Update the device status for the given set of devices to "accepted"

    :param api_deviceauth: testutils.api.client.ApiClient setup and authorized
                           to use the deviceauth management api,
                           i.e. api_client.with_auth(api_token)
    :param devices: list of dict-type devices as returned by
                    GET /api/management/v1/devauth/devices
                    If left None, all pending devices are accepted.
    """
    if devices is None:
        rsp = api_deviceauth.call(
            "GET", deviceauth.URL_MGMT_DEVICES, qs_params={"status": "pending"}
        )
        assert rsp.status_code == 200
        devices = rsp.json()

    for device in devices:
        rsp = api_deviceauth.call(
            "PUT",
            deviceauth.URL_AUTHSET_STATUS.format(
                did=device["id"], aid=device["auth_sets"][0]["id"]
            ),
            body={"status": "accepted"},
        )
        assert rsp.status_code == 204


def assert_inventory_updated(api_inventory, num_devices, timeout=TIMEOUT):
    """
    Polls the inventory every second, checking the returned "updated_ts"
    properties for each device, waiting for the value to update to a value
    later than when this function was called.
    :param api_inventory: testutils.api.client.ApiClient setup and authorized
                          to use the inventory management api,
                          i.e. api_client.with_auth(api_token).
    :param num_devices: the number of devices to wait for.
    :param timeout: optional timeout (defaults to 5min).
    """
    update_after = datetime.now(timezone.utc)
    deadline = update_after + timeout
    num_updated = 0
    while num_updated < num_devices:
        if datetime.now(timezone.utc) > deadline:
            pytest.fail("timeout waiting for devices to submit inventory")

        rsp = api_inventory.call(
            "GET", inventory.URL_DEVICES, qs_params={"per_page": num_devices * 2},
        )
        assert rsp.status_code == 200
        dev_invs = rsp.json()
        assert (
            len(dev_invs) <= num_devices
        ), "Received more devices from inventory than there exists"
        if len(dev_invs) < num_devices:
            time.sleep(1)
            continue
        # Check if inventories has been updated since starting this loop
        num_updated = 0
        for device in dev_invs:
            # datetime does not have an RFC3339 parser, but we can convert
            # the timestamp to a compatible ISO format by removing
            # fractions and replacing the Zulu timezone with GMT.
            updated_ts = datetime.fromisoformat(
                device["updated_ts"].split(".")[0] + "+00:00"
            )
            if updated_ts > update_after:
                num_updated += 1
            else:
                time.sleep(1)
                break


def assert_successful_deployment(api_deployments, deployment_id, timeout=TIMEOUT):
    """
    Waits for the ongoing deployment (specified by deployment_id) to finish
    and asserting all devices were successfully upgraded.
    :param api_deployments: testutils.api.client.ApiClient setup and authorized
                             to use the deployments management api,
                             i.e. api_client.with_auth(api_token)
    :param deployment_id: deployment id to watch
    :param timeout: optional timeout value to wait for deployment (defaults to 5min)
    """
    deadline = datetime.now() + timeout
    while True:
        rsp = api_deployments.call(
            "GET", deployments.URL_DEPLOYMENTS_ID.format(id=deployment_id)
        )
        assert rsp.status_code == 200

        dpl = rsp.json()
        if dpl["status"] == "finished":
            rsp = api_deployments.call(
                "GET", deployments.URL_DEPLOYMENTS_STATISTICS.format(id=deployment_id),
            )
            assert rsp.status_code == 200
            assert rsp.json()["failure"] == 0
            assert rsp.json()["success"] == dpl["device_count"]
            break
        elif datetime.now() > deadline:
            pytest.fail("timeout: Waiting for devices to update")
        else:
            time.sleep(1)


class TestClientCompatibilityBase:
    """
    This class contains compatibility tests implementation for assessing
    server compatibility with older clients.
    """

    def compatibility_test_impl(self, env):
        """
        The actual test implementation:
         - Accept devices
         - Verify devices patches inventory
         - Perform a noop rootfs update and verify the update was successful
        """
        gateway_addr = env.get_mender_gateway()
        URL_MGMT_USERADM = useradm.URL_MGMT.replace("mender-api-gateway", gateway_addr)
        URL_MGMT_DEVAUTH = deviceauth.URL_MGMT.replace(
            "mender-api-gateway", gateway_addr
        )
        URL_MGMT_INVNTRY = inventory.URL_MGMT.replace(
            "mender-api-gateway", gateway_addr
        )
        URL_MGMT_DPLMNTS = deployments.URL_MGMT.replace(
            "mender-api-gateway", gateway_addr
        )
        api_useradmm = ApiClient(base_url=URL_MGMT_USERADM)

        rsp = api_useradmm.call(
            "POST", useradm.URL_LOGIN, auth=(env.user.name, env.user.pwd)
        )
        assert rsp.status_code == 200, "Failed to log in test user"
        api_token = rsp.text

        api_useradmm = api_useradmm.with_auth(api_token)
        api_devauthm = ApiClient(base_url=URL_MGMT_DEVAUTH).with_auth(api_token)
        api_inventory = ApiClient(base_url=URL_MGMT_INVNTRY).with_auth(api_token)
        api_deployments = ApiClient(base_url=URL_MGMT_DPLMNTS).with_auth(api_token)

        deadline = datetime.now() + TIMEOUT
        devices = []
        while True:
            rsp = api_devauthm.call(
                "GET", deviceauth.URL_MGMT_DEVICES, qs_params={"status": "pending"}
            )
            assert rsp.status_code == 200

            devices = rsp.json()
            assert len(devices) <= len(env.devices)

            if len(devices) == len(env.devices):
                break
            elif datetime.now() > deadline:
                pytest.fail("timeout waiting for devices to connect to server")
            else:
                time.sleep(1)

        # Accept all devices
        accept_devices(api_devauthm)

        # Check that inventory gets updated successfully
        assert_inventory_updated(api_inventory, len(devices))

        # Deploy an update with an artifact with an empty payload which
        # effectively performs a "rollback" marking the unchanged
        # passive partition as the new updated active partition.
        artifact = Artifact(
            artifact_name="rootfs-noop-update",
            device_types=["qemux86-64"],
            payload="",
            payload_type="rootfs-image",
        )
        artifact_file = artifact.make()
        rsp = api_deployments.call(
            "POST",
            deployments.URL_DEPLOYMENTS_ARTIFACTS,
            files={
                "artifact": (
                    "artifact.mender",
                    artifact_file,
                    "application/octet-stream",
                ),
            },
        )
        assert rsp.status_code == 201

        rsp = api_deployments.call(
            "POST",
            deployments.URL_DEPLOYMENTS,
            body={
                "artifact_name": "rootfs-noop-update",
                "devices": [device["id"] for device in devices],
                "name": "noop_deployment",
            },
        )
        assert rsp.status_code == 201

        deployment_id = rsp.headers.get("Location").split("/")[-1]
        assert_successful_deployment(api_deployments, deployment_id)


class TestClientCompatibilityOpenSource(TestClientCompatibilityBase):
    def test_compatibility(self, setup_os_compat):
        self.compatibility_test_impl(setup_os_compat)


class TestClientCompatibilityEnterprise(TestClientCompatibilityBase):
    def test_enterprise_compatibility(self, setup_ent_compat):
        self.compatibility_test_impl(setup_ent_compat)
