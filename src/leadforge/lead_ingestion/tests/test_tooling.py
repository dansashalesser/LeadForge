"""Smoke tests proving async test support and HTTP-boundary mocking are wired."""

import httpx
import respx


# Verifies: specs/lead-source-adapters/requirements.md#1.1
async def test_async_tests_run_and_respx_intercepts_httpx() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://provider.invalid/ping").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get("https://provider.invalid/ping")

    assert route.called
    assert response.json() == {"ok": True}
