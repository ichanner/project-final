import httpx

USER_AGENT = "WebHarvest/0.1 (+https://example.com/webharvest)"


DEFAULT_TIMEOUT = 20.0


async def fetch(url: str) -> tuple[int, str]:
    """Fetch a URL and return (status_code, html). Raises on transport error."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
    ) as client:
        resp = await client.get(url)
        return resp.status_code, resp.text
