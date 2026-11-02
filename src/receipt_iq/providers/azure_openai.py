"""A GPT model on Azure OpenAI reads the receipt image directly, with no OCR step.

    image ──► chat completions (strict JSON schema) ──► Receipt

Same prompt as Gemini, so their scores compare fairly. Unlike the free tiers this
spends real credit, so the model is cheap, output is capped (`max_output_tokens`)
and a reply cut off at the cap fails the receipt instead of being retried.

The endpoint, key and deployment name come from the AZURE_OPENAI_ENDPOINT,
AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT environment variables, and the key is
sent in a request header, never in the URL.

GPT-5 models take no temperature setting (only the default), so repeat runs of a
receipt aren't guaranteed to match; the cache keeps the first answer.
"""

import base64
import os
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from receipt_iq.evaluation import ExtractionError, TemporaryError
from receipt_iq.providers.azure_di import Response, http_request
from receipt_iq.providers.shared import RESPONSE_SCHEMA, VISION_PROMPT, mime_type
from receipt_iq.schema import Receipt

PROMPT_VERSION = "v1"


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert our JSON Schema to what OpenAI's strict mode requires: every property
    listed as required (nullable ones may still be null) and no extra properties."""
    converted = dict(schema)
    if "properties" in schema:
        converted["properties"] = {
            name: to_strict_schema(sub) for name, sub in schema["properties"].items()
        }
        converted["required"] = list(schema["properties"])
        converted["additionalProperties"] = False
    if "items" in schema:
        converted["items"] = to_strict_schema(schema["items"])
    return converted


class AzureOpenAI:
    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        reasoning_effort: str = "low",
        # Room for reasoning plus a long receipt, and a ceiling on the cost of one call.
        max_output_tokens: int = 4000,
        # The deployment's limit is 20 requests a minute.
        min_interval: float = 3.0,
        max_retries: int = 4,
        timeout: float = 180,
        send: Callable[
            [str, dict[str, Any] | None, dict[str, str], float], Response
        ] = http_request,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        key = api_key or os.environ.get("AZURE_OPENAI_KEY")
        deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        if not endpoint or not key or not deployment:
            raise RuntimeError(
                "set the AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY and "
                "AZURE_OPENAI_DEPLOYMENT environment variables"
            )
        self.name = deployment
        self.deployment = deployment
        self.reasoning_effort = reasoning_effort
        self.version = f"prompt-{PROMPT_VERSION}.reasoning-{reasoning_effort}"
        self._url = f"{endpoint.rstrip('/')}/openai/v1/chat/completions"
        self._headers = {"api-key": key}
        self.max_output_tokens = max_output_tokens
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        # Injected so tests run without network access or real waiting.
        self._send = send
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def extract(self, image: bytes) -> Receipt:
        data = base64.b64encode(image).decode()
        body = {
            "model": self.deployment,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {
                            "type": "image_url",
                            # Small print needs the full-resolution reading.
                            "image_url": {
                                "url": f"data:{mime_type(image)};base64,{data}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "receipt",
                    "strict": True,
                    "schema": to_strict_schema(RESPONSE_SCHEMA),
                },
            },
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": self.max_output_tokens,
        }
        reply = self._request(body)
        try:
            choice = reply["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError) as failure:
            raise ExtractionError("no content in reply") from failure
        if not text:
            # Usually the output cap ("length") or the content filter.
            raise ExtractionError(f"empty reply ({choice.get('finish_reason')})")
        try:
            return Receipt.model_validate_json(text)
        except ValidationError as error:
            raise ExtractionError(f"invalid output: {error}") from error

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                status, headers, reply = self._send(self._url, body, self._headers, self.timeout)
            except TemporaryError as failure:
                status, headers, reply = 0, {}, {"error": {"message": str(failure)}}
            if status == 200:
                return reply
            message = reply.get("error", {}).get("message")
            if status in (401, 403, 404):
                # A setup problem (wrong key or deployment, credit used up), not a bad receipt.
                raise RuntimeError(f"azure openai rejected the request (HTTP {status}): {message}")
            if status in (0, 429) or status >= 500:
                if attempt < self.max_retries:
                    delay = headers.get("retry-after", "")
                    self._sleep(float(delay) if delay.isdigit() else 5 * 2**attempt)
                continue
            raise ExtractionError(f"azure openai HTTP {status}: {message}")
        raise TemporaryError(f"azure openai unavailable: HTTP {status} after retries: {message}")

    def _wait_for_slot(self) -> None:
        """Space requests at least `min_interval` seconds apart."""
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + self.min_interval - now
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()
