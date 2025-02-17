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
import pytest
import base64
import io
import uuid
from urllib import parse
import re
import time

from PIL import Image
from pyzbar.pyzbar import decode
import pyotp

from testutils.infra.smtpd_mock import smtp_server
from testutils.api.client import ApiClient
from testutils.infra.cli import CliUseradm, CliTenantadm
import testutils.api.useradm as useradm
import testutils.api.tenantadm as tenantadm
from testutils.common import mongo, clean_mongo, create_org, create_user

uadm = ApiClient(useradm.URL_MGMT)

TFA_ENABLED = "enabled"
TFA_DISABLED = "disabled"
TFA_UNVERIFIED = "unverified"


@pytest.fixture(scope="function")
def clean_migrated_mongo(clean_mongo):
    useradm_cli = CliUseradm()
    tenantadm_cli = CliTenantadm()

    for t in ["tenant1", "tenant2"]:
        useradm_cli.migrate(t)
        tenantadm_cli.migrate()

    yield clean_mongo


@pytest.fixture(scope="function")
def tenants_users(clean_migrated_mongo):
    tenants = []
    for _ in range(2):
        uuidv4 = str(uuid.uuid4())
        tenant, username, password = (
            "test.mender.io-" + uuidv4,
            "ci.email.tests+" + uuidv4 + "@mender.io",
            "secretsecret",
        )
        # Create tenant with two users
        tenant = create_org(tenant, username, password, "enterprise")
        tenant.users.append(
            create_user(
                "ci.email.tests+" + uuidv4 + "-user2@mender.io", password, tenant.id
            )
        )
        tenants.append(tenant)

    yield tenants


class Test2FAEnterprise:
    def _login(self, user, totp=None):
        body = {}
        if totp is not None:
            body = {"token2fa": totp}

        r = uadm.call("POST", useradm.URL_LOGIN, auth=(user.name, user.pwd), body=body)
        return r

    def _verify(self, utoken, totp):
        body = {"token2fa": totp}

        r = uadm.with_auth(utoken).call("PUT", useradm.URL_2FAVERIFY, body=body)
        return r

    def _toggle_tfa(self, utoken, user_id, on=True):
        if on:
            url = useradm.URL_2FA_ENABLE
        else:
            url = useradm.URL_2FA_DISABLE

        r = uadm.with_auth(utoken).call("POST", url, path_params={"id": user_id})
        return r

    def _qr_dec(self, qr_b64):
        # decode png from temp inmem file
        b = base64.b64decode(qr_b64)
        f = io.BytesIO(b)
        image = Image.open(f)

        # decode qr code - results in a otpauth://... url in 'data' bytes
        dec = decode(image)[0]

        qs = parse.urlsplit(dec.data).query

        secret_b32 = parse.parse_qs(qs)[b"secret"][0]

        return secret_b32

    def _get_user(self, utoken):
        r = uadm.with_auth(utoken).call("GET", useradm.URL_USERS_ID.format(id="me"))
        assert r.status_code == 200
        return r.json()

    def _make_2fa_settings(self, user_statuses):
        s = {}
        for k, v in user_statuses.items():
            s[k + "_2fa"] = v

        return s

    def test_enable_disable(self, tenants_users, smtp_server):
        user_2fa = tenants_users[0].users[0]
        user_no_2fa = tenants_users[0].users[1]

        r = self._login(user_2fa)
        assert r.status_code == 200
        user_2fa_tok = r.text

        # some error scenarios related to invalid 2fa state
        # before email verification - can't touch settings
        for on in [True, False]:
            r = self._toggle_tfa(user_2fa_tok, user_2fa.id, on=True)
            assert r.status_code == 403

        # /2faqr available only in 'unverified' state
        r = uadm.with_auth(user_2fa_tok).call("GET", useradm.URL_2FAQR)

        assert r.status_code == 400

        # /verify available only in 'unverified' state
        r = uadm.with_auth(user_2fa_tok).call("GET", useradm.URL_2FAQR)

        assert r.status_code == 400

        # verify user email address
        r = uadm.post(useradm.URL_VERIFY_EMAIL_START, body={"email": user_2fa.name})
        assert r.status_code == 202
        # wait for the verification email
        message = None
        for i in range(15):
            messages = smtp_server.filtered_messages(user_2fa.name)
            if len(messages) > 0:
                message = messages[0]
                break
            time.sleep(1)
        # be sure we received the email
        assert message is not None
        assert message.data != ""
        # extract the secret hash from the link
        match = re.search(
            r"https://hosted.mender.io/ui/#/activate/([a-z0-9\-]+)",
            message.data.decode("utf-8"),
        )
        secret_hash = match.group(1)
        assert secret_hash != ""
        # complete the email address
        r = uadm.post(
            useradm.URL_VERIFY_EMAIL_COMPLETE, body={"secret_hash": secret_hash}
        )
        assert r.status_code == 204

        # enable tfa for 1 user, straight login still works, token is not verified
        r = self._toggle_tfa(user_2fa_tok, user_2fa.id, on=True)
        assert r.status_code == 200

        r = self._login(user_2fa)
        assert r.status_code == 200

        # get the user info and verify 2fa status
        user = self._get_user(user_2fa_tok)
        assert user["tfa_status"] == TFA_UNVERIFIED

        # grab qr code, extract token, calc TOTP
        r = uadm.with_auth(user_2fa_tok).call("GET", useradm.URL_2FAQR)

        assert r.status_code == 200

        secret = self._qr_dec(r.json()["qr"])
        totp = pyotp.TOTP(secret)
        tok = totp.now()

        # verify token
        r = self._verify(user_2fa_tok, tok)
        assert r.status_code == 202

        # get the user info and verify 2fa status
        user = self._get_user(user_2fa_tok)
        assert user["tfa_status"] == TFA_ENABLED

        # login with totp succeeds
        r = self._login(user_2fa, totp=tok)
        assert r.status_code == 200

        # already enabled - can't enable twice
        r = self._toggle_tfa(user_2fa_tok, user_2fa.id, on=True)
        assert r.status_code == 400

        # logi without otp now does not work
        r = self._login(user_2fa)
        assert r.status_code == 401

        # the other user, and other tenant's users, are unaffected
        r = self._login(user_no_2fa)
        assert r.status_code == 200
        user_no_2fa_tok = r.text

        # other users can't change our settings
        r = self._toggle_tfa(user_no_2fa_tok, user_2fa.id, on=False)
        assert r.status_code == 401

        # get the user info and verify 2fa status
        user = self._get_user(user_2fa_tok)
        assert user["tfa_status"] == TFA_ENABLED

        for other_user in tenants_users[1].users:
            r = self._login(other_user)
            assert r.status_code == 200

        # after disabling - straight login works again
        r = self._toggle_tfa(user_2fa_tok, "me", on=False)
        assert r.status_code == 200

        r = self._login(user_2fa)
        assert r.status_code == 200

        # get the user info and verify 2fa status
        user = self._get_user(user_2fa_tok)
        assert user["tfa_status"] == TFA_DISABLED

        # although POST /settings is still functional,
        # it will not save any 2fa settings, and will not break
        # any user's statuses

        # simulate overwriting user statuses - no effect
        signore = self._make_2fa_settings(
            {user_2fa.id: TFA_ENABLED, user_no_2fa.id: TFA_ENABLED}
        )
        r = uadm.with_auth(user_2fa_tok).call("POST", useradm.URL_SETTINGS, signore)
        assert r.status_code == 201

        # get the user info and verify 2fa status
        user = self._get_user(user_2fa_tok)
        assert user["tfa_status"] == TFA_DISABLED
