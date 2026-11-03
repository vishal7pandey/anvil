import json

import httpx

import anvil.runtime.agent as runtime_agent


def test_model_sends_run_id_as_litellm_spend_log_metadata(monkeypatch):
    run_id = "run-123"
    captured = {}

    def handle(request):
        body = json.loads(request.content)
        captured["metadata"] = body.get("metadata")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "anvil-main",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    chat_openai = runtime_agent.ChatOpenAI
    monkeypatch.setattr(
        runtime_agent,
        "ChatOpenAI",
        lambda **kwargs: chat_openai(**kwargs, http_client=client),
    )
    try:
        model = runtime_agent._model(
            runtime_agent.RunConfig(run_id=run_id, repo="example/repo"),
            "anvil-main",
        )
        model.invoke("metadata propagation check")
    finally:
        client.close()

    assert captured["metadata"]["spend_logs_metadata"]["run_id"] == run_id
