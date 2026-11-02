import httpx
import pytest
import respx
from anvil.kb.config import KBSettings
from anvil.kb.embeddings import DeterministicEmbedder, GatewayEmbedder


@respx.mock
def test_embed_requests_gateway_alias_and_orders_by_index():
    route = respx.post("http://gw/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 0.0, 0.0, 2.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
                ]
            },
        )
    )
    settings = KBSettings(gateway_url="http://gw", gateway_key="test-key", embed_dim=4)
    with httpx.Client() as client:
        embedder = GatewayEmbedder(settings, client=client)
        vectors = embedder.embed(["a", "b"])

    assert vectors == [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 2.0]]
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-key"
    assert b'"model":"anvil-embed"' in request.content


@respx.mock
def test_wrong_dimension_is_rejected():
    respx.post("http://gw/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
        )
    )
    settings = KBSettings(gateway_url="http://gw", embed_dim=1536)
    with httpx.Client() as client:
        embedder = GatewayEmbedder(settings, client=client, max_retries=1)
        with pytest.raises(RuntimeError, match="failed after 1 attempts"):
            embedder.embed(["a"])


def test_deterministic_embedder_is_stable_and_normalised():
    embedder = DeterministicEmbedder(dim=8)
    first = embedder.embed(["rtems interrupt latency"])[0]
    second = embedder.embed(["rtems interrupt latency"])[0]
    assert first == second
    assert len(first) == 8
    assert pytest.approx(sum(value * value for value in first), rel=1e-6) == 1.0
