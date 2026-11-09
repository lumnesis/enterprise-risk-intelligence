"""HTTP boundary for the public Coinbase Status endpoint (latest 50 incidents)."""

from typing import Any

import httpx

from enterprise_risk.config import SOURCE_URL


class SourceError(Exception):
    """Safe-to-display error without response bodies or request credentials."""


def fetch_incidents(client: httpx.Client) -> dict[str, Any]:
    try:
        response = client.get(SOURCE_URL, timeout=30.0)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise SourceError("Coinbase Status request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise SourceError(f"Coinbase Status returned HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise SourceError("Could not connect to Coinbase Status") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError("Coinbase Status returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("incidents"), list):
        raise SourceError("Expected a JSON object with an incidents array")
    return payload
