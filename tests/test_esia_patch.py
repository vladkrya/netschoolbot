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


async def test_patch_anomaly_reaction_verify_403_with_failed_to_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"failed": "INVALID_OTP"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await client.post(f"{esia.ESIA_LOGIN_API}/anomaly-reaction/verify", json={"code": "123456", "guid": "abc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] == "INVALID_OTP"

