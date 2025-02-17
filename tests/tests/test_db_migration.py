# Copyright 2022 Northern.tech AS
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import os
import tempfile
import shutil

import pytest

from .. import conftest
from ..common_setup import (
    setup_with_legacy_client,
    enterprise_with_legacy_client,
)
from .common_update import update_image, common_update_procedure
from ..MenderAPI import DeviceAuthV2, Deployments, logger
from .mendertesting import MenderTesting


class BaseTestDBMigration(MenderTesting):
    def ensure_persistent_conf_script(self, dir):
        # Because older versions of Yocto branches did not split mender.conf
        # into /etc/mender/mender.conf and /data/mender/mender.conf, we need to
        # provide the content of the second file ourselves.
        name = os.path.join(dir, "ArtifactInstall_Enter_00_ensure_persistent_conf")
        with open(name, "w") as fd:
            fd.write(
                """#!/bin/sh

set -e

if ! [ -f /data/mender/mender.conf ]; then
    (
         echo '{'
         grep RootfsPart /etc/mender/mender.conf |sed -e '${s/,$//}'
         echo '}'
    ) > /data/mender/mender.conf
fi
exit 0
"""
            )
        return name

    def do_test_migrate_from_legacy_mender_v1_failure(
        self, env, valid_image_with_mender_conf
    ):
        """
        Start a legacy client (1.7.0) first and update it to the new one.

        The test starts a setup with the 1.7.0 client and then updates it to
        the current version. The update is failing first (due to failure
        returned inside the artifact commit enter state script).
        After the failed first update we are updating cient (1.7.0) again,
        and this time the update should succeed.
        """

        mender_device = env.device
        devauth = DeviceAuthV2(env.auth)
        deploy = Deployments(env.auth, devauth)

        dirpath = tempfile.mkdtemp()
        script_content = "#!/bin/sh\nexit 1\n"
        with open(os.path.join(dirpath, "ArtifactCommit_Enter_01"), "w") as fd:
            fd.write(script_content)

        active_part = mender_device.get_active_partition()

        ensure_persistent_conf = self.ensure_persistent_conf_script(dirpath)

        mender_conf = mender_device.run("cat /etc/mender/mender.conf")
        valid_image = valid_image_with_mender_conf(mender_conf)

        # first start with the failed update
        host_ip = env.get_virtual_network_host_ip()
        with mender_device.get_reboot_detector(host_ip) as reboot:
            deployment_id, _ = common_update_procedure(
                valid_image,
                scripts=[
                    ensure_persistent_conf,
                    os.path.join(dirpath, "ArtifactCommit_Enter_01"),
                ],
                version=2,
                devauth=devauth,
                deploy=deploy,
            )

            logger.info("waiting for system to reboot twice")
            reboot.verify_reboot_performed(number_of_reboots=2)

            assert mender_device.get_active_partition() == active_part
            deploy.check_expected_statistics(deployment_id, "failure", 1)

        # do the next update, this time succesful
        update_image(
            mender_device,
            host_ip,
            scripts=[ensure_persistent_conf],
            install_image=valid_image,
            version=2,
            devauth=devauth,
            deploy=deploy,
        )

    def do_test_migrate_from_legacy_mender_v1_success(
        self, env, valid_image_with_mender_conf
    ):
        """
        Start a legacy client (1.7.0) first and update it to the new one.

        The test starts a setup with the 1.7.0 client and then updates it to
        the current version. After the first successful update, we are updating
        the client for the second time, to make sure the DB migration has not left
        any traces in the database that are causing issues.
        """

        mender_device = env.device
        devauth = DeviceAuthV2(env.auth)
        deploy = Deployments(env.auth, devauth)

        tmpdir = tempfile.mkdtemp()
        test_log = "/var/lib/mender/migration_state_scripts.log"
        try:
            ensure_persistent_conf = self.ensure_persistent_conf_script(tmpdir)

            # Test that state scripts are also executed correctly.
            scripts = ["ArtifactInstall_Enter_00", "ArtifactCommit_Enter_00"]
            scripts_paths = []
            for script in scripts:
                script_path = os.path.join(tmpdir, script)
                scripts_paths += [script_path]
                with open(script_path, "w") as fd:
                    fd.write("#!/bin/sh\necho $(basename $0) >> %s\n" % test_log)

            mender_conf = mender_device.run("cat /etc/mender/mender.conf")
            valid_image = valid_image_with_mender_conf(mender_conf)

            # do the succesful update twice
            host_ip = env.get_virtual_network_host_ip()
            update_image(
                mender_device,
                host_ip,
                install_image=valid_image,
                scripts=[ensure_persistent_conf] + scripts_paths,
                version=2,
                devauth=devauth,
                deploy=deploy,
            )
            assert mender_device.run("cat %s" % test_log).strip() == "\n".join(scripts)

            update_image(
                mender_device,
                host_ip,
                install_image=valid_image,
                scripts=[ensure_persistent_conf] + scripts_paths,
                version=2,
                devauth=devauth,
                deploy=deploy,
            )
            assert mender_device.run("cat %s" % test_log).strip() == "\n".join(
                scripts
            ) + "\n" + "\n".join(scripts)

        finally:
            shutil.rmtree(tmpdir)


class TestDBMigrationOpenSource(BaseTestDBMigration):
    def test_migrate_from_legacy_mender_v1_failure(
        self, setup_with_legacy_client, valid_image_with_mender_conf
    ):
        self.do_test_migrate_from_legacy_mender_v1_failure(
            setup_with_legacy_client, valid_image_with_mender_conf
        )

    def test_migrate_from_legacy_mender_v1_success(
        self, setup_with_legacy_client, valid_image_with_mender_conf
    ):
        self.do_test_migrate_from_legacy_mender_v1_success(
            setup_with_legacy_client, valid_image_with_mender_conf
        )


class TestDBMigrationEnterprise(BaseTestDBMigration):
    def test_migrate_from_legacy_mender_v1_failure(
        self, enterprise_with_legacy_client, valid_image_with_mender_conf
    ):
        self.do_test_migrate_from_legacy_mender_v1_failure(
            enterprise_with_legacy_client, valid_image_with_mender_conf
        )

    def test_migrate_from_legacy_mender_v1_success(
        self, enterprise_with_legacy_client, valid_image_with_mender_conf
    ):
        self.do_test_migrate_from_legacy_mender_v1_success(
            enterprise_with_legacy_client, valid_image_with_mender_conf
        )
