"""Optional text-only classifier. Outputs are review signals, never punishments."""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from openai import APIError, AsyncOpenAI

from aegis.config import AIConfig

log = logging.getLogger(__name__)
CANDIDATE = re.compile(
    r"\b(kill|hate|die|threat|rape|suicide|marunga|maarunga|madarchod|bhenchod)\b"
    r"|मार|नफरत|आत्महत्या|मादरचोद|बहनचोद",
    re.I,
)


@dataclass(frozen=True)
class AIResult:
    flagged: bool
    categories: tuple[str, ...]
    scores: dict[str, float] = field(default_factory=dict)
    model: str = ""

    def needs_review(self, threshold: float) -> bool:
        return self.flagged or any(score >= threshold for score in self.scores.values())

    @property
    def support_signal(self) -> bool:
        return any(c.startswith("self-harm") for c in self.categories)


class AIClassifier:
    def __init__(
        self, config: AIConfig, api_key: str, model: str, *, client: AsyncOpenAI | None = None
    ):
        self.config = config
        self.model = model
        self.client = client or (
            AsyncOpenAI(api_key=api_key, timeout=config.timeout_seconds, max_retries=0)
            if api_key
            else None
        )
        self.cache: OrderedDict[str, tuple[float, AIResult]] = OrderedDict()
        self.requests: deque[float] = deque()
        self.daily_count = 0
        self.day = ""
        self.active = 0
        self.blocked_until = 0.0
        self.metrics: Counter = Counter()

    def eligible(self, content: str) -> bool:
        return bool(content.strip()) and (
            bool(CANDIDATE.search(content)) or random.random() < self.config.sample_rate
        )

    async def classify(self, guild_id: int, content: str) -> AIResult | None:
        if self.client is None or not content.strip():
            self.metrics["disabled"] += 1
            return None
        # Do not collapse punctuation, case, or whitespace here: context can matter.
        content = content[:4000]
        key = hashlib.sha256(f"{guild_id}:{self.model}:{content}".encode()).hexdigest()
        now = time.monotonic()
        cached = self.cache.get(key)
        if cached and now - cached[0] < self.config.cache_ttl_seconds:
            self.cache.move_to_end(key)
            self.metrics["cache_hits"] += 1
            return cached[1]
        if cached:
            del self.cache[key]
        day = datetime.now(UTC).date().isoformat()
        if day != self.day:
            self.day, self.daily_count = day, 0
        while self.requests and now - self.requests[0] >= 60:
            self.requests.popleft()
        # No unbounded semaphore waiters: shed excess work and expose the count.
        if now < self.blocked_until or self.active >= self.config.concurrency:
            self.metrics["unavailable"] += 1
            return None
        if (
            len(self.requests) >= self.config.requests_per_minute
            or self.daily_count >= self.config.requests_per_day
        ):
            self.metrics["budget_skips"] += 1
            return None
        self.requests.append(now)
        self.daily_count += 1
        self.active += 1
        try:
            response = await self.client.moderations.create(model=self.model, input=content)
            item = response.results[0]
            flags = item.categories.model_dump(by_alias=True)
            scores = item.category_scores.model_dump(by_alias=True)
            result = AIResult(
                flagged=item.flagged,
                categories=tuple(k for k, v in flags.items() if v is True),
                scores={k: float(v) for k, v in scores.items() if isinstance(v, (int, float))},
                model=response.model,
            )
            self.cache[key] = (time.monotonic(), result)
            while len(self.cache) > self.config.cache_size:
                self.cache.popitem(last=False)
            self.metrics["classified"] += 1
            return result
        except (APIError, IndexError, ValueError, TypeError, AttributeError) as error:
            # API exception strings may contain request details: log only the type.
            self.blocked_until = time.monotonic() + 60
            self.metrics["errors"] += 1
            log.warning("AI unavailable; local rules continue (%s)", type(error).__name__)
            return None
        finally:
            self.active -= 1

    def clear_guild(self, guild_id: int) -> None:
        # Cache keys are hashes without identifiable guild prefixes. Clear all
        # bounded cache entries when any guild leaves rather than retain its data.
        self.cache.clear()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
