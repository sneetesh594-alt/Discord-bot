import asyncio
import json

import httpx
from openai import AsyncOpenAI

from aegis.ai import AIClassifier, AIResult
from aegis.config import AIConfig

CATEGORIES = (
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/intent",
    "self-harm/instructions",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
)


def response(category="hate", score=0.9):
    return {
        "id": "modr-test",
        "model": "omni-moderation-test",
        "results": [
            {
                "flagged": True,
                "categories": {k: k == category for k in CATEGORIES},
                "category_scores": {k: score if k == category else 0.0 for k in CATEGORIES},
                "category_applied_input_types": {k: ["text"] for k in CATEGORIES},
            }
        ],
    }


def client(handler):
    return AsyncOpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_real_sdk_contract_and_cache_are_guild_scoped():
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.path == "/v1/moderations"
        return httpx.Response(200, json=response())

    classifier = AIClassifier(AIConfig(), "", "omni-moderation-latest", client=client(handler))
    first = await classifier.classify(1, "test text")
    assert first.needs_review(0.7) and first.categories == ("hate",)
    assert first.scores["hate"] == 0.9
    assert await classifier.classify(1, "test text") == first
    await classifier.classify(2, "test text")
    assert len(calls) == 2
    assert calls[0] == {"model": "omni-moderation-latest", "input": "test text"}
    await classifier.close()


async def test_rate_budget_skips_without_sending_a_request():
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=response())

    config = AIConfig(requests_per_minute=1)
    classifier = AIClassifier(config, "", "model", client=client(handler))
    assert await classifier.classify(1, "first")
    assert await classifier.classify(1, "second") is None
    assert classifier.metrics["budget_skips"] == 1 and count == 1
    await classifier.close()


async def test_api_outage_opens_circuit_and_does_not_return_clean_result():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    classifier = AIClassifier(AIConfig(), "", "model", client=client(handler))
    assert await classifier.classify(1, "first") is None
    assert await classifier.classify(1, "second") is None
    assert calls == 1 and classifier.metrics["errors"] == 1
    assert classifier.metrics["unavailable"] == 1
    await classifier.close()


async def test_busy_classifier_sheds_work_instead_of_building_waiters():
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=response())

    classifier = AIClassifier(AIConfig(concurrency=1), "", "model", client=client(handler))
    first = asyncio.create_task(classifier.classify(1, "first"))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert await classifier.classify(1, "second") is None
    release.set()
    assert await first
    await classifier.close()


def test_sampling_is_explicit_and_self_harm_routes_to_support():
    classifier = AIClassifier(AIConfig(sample_rate=0), "", "model")
    assert not classifier.eligible("ordinary conversation")
    assert classifier.eligible("a threat to kill")
    assert classifier.eligible("मैं आत्महत्या के बारे में सोच रहा हूँ")
    assert AIResult(True, ("self-harm/intent",)).support_signal
    assert not AIResult(True, ("hate",)).support_signal


async def test_cache_is_bounded_and_text_is_limited():
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    classifier = AIClassifier(AIConfig(cache_size=1), "", "model", client=client(handler))
    await classifier.classify(1, "a" * 5000)
    await classifier.classify(1, "other")
    assert len(calls[0]["input"]) == 4000 and len(classifier.cache) == 1
    classifier.clear_guild(1)
    assert not classifier.cache
    await classifier.close()
