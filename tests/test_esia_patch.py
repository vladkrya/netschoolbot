import json
import httpx
import pytest

from app.netschool.patches import esia


@pytest.fixture(autouse=True)
def apply_patch():
    esia.apply()


async def test_patch_supplies_json_body_for_verify():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"redirect_url": "https://school.example/ok"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.post(
            f"{esia.ESIA_LOGIN_API}/otp/verify",
            params={"code": "123456"},
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert len(seen) == 1
        assert json.loads(seen[0].read()) == {"code": "123456"}


async def test_patch_converts_202_with_next_step_to_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"action": "MAX_QUIZ"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await client.post(f"{esia.ESIA_LOGIN_API}/otp/verify", params={"code": "123456"})
        assert resp.status_code == 200


async def test_patch_converts_403_with_failed_to_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"failed": "INVALID_OTP", "mfa_details": {"otp_details": {"verify_attempts_left": 2}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await client.post(f"{esia.ESIA_LOGIN_API}/otp/verify", params={"code": "123456"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] == "INVALID_OTP"


async def test_patch_anomaly_reaction_verify_does_not_turn_failure_into_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"failed": "INVALID_OTP"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await client.post(f"{esia.ESIA_LOGIN_API}/anomaly-reaction/verify", json={"code": "123456", "guid": "abc"})
        assert resp.status_code == 403
        data = resp.json()
        assert data["failed"] == "INVALID_OTP"


@pytest.mark.parametrize(("mfa_type", "path"), [("SMS", "otp"), ("TOTP", "totp"), ("TTP", "totp"), ("MAX", "otp-max")])
@pytest.mark.parametrize("status", [202, 404])
async def test_mfa_sends_code_once_to_the_matching_endpoint(mfa_type, path, status):
    from netschoolpy.client import NetSchool
    from netschoolpy.exceptions import MFAError

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"redirect_url": "https://school.example/ok"})

    async def get_code(*_):
        return "012345"

    school = NetSchool("https://school.example")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = {"mfa_details": {"type": mfa_type}}
        if status == 404:
            with pytest.raises(MFAError, match="endpoint"):
                await school._handle_esia_mfa(client, data, otp_callback=get_code)
        else:
            assert await school._handle_esia_mfa(client, data, otp_callback=get_code) == "https://school.example/ok"
    assert len(seen) == 1
    assert str(seen[0].url).split("?")[0] == f"{esia.ESIA_LOGIN_API}/{path}/verify"
    assert seen[0].url.params["code"] == "012345"
