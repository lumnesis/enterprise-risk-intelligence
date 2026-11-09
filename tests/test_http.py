import httpx
import pytest

from enterprise_risk.ingestion.coinbase import SourceError, fetch_incidents


def test_fetch(payload):
    def handler(request):
        assert request.url.path == "/api/v2/incidents.json"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_incidents(client) == payload


@pytest.mark.parametrize("status", [403, 404, 429, 500, 503])
def test_http_errors(status):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        with pytest.raises(SourceError, match=f"HTTP {status}"):
            fetch_incidents(client)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_errors(error):
    def handler(request):
        raise error("sensitive transport details", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as caught:
            fetch_incidents(client)
        assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("content", [b"not-json", b"[]", b"{}", b'{"incidents": null}'])
def test_malformed_response(content):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))
    ) as client:
        with pytest.raises(SourceError):
            fetch_incidents(client)
