import json
from typing import Awaitable, Callable
import httpx
from .errors import ServiceError


async def complete_json(client: httpx.AsyncClient, api_base: str, api_key: str, payload: dict,
                        on_text, on_reasoning=None, *, request_id: str | None = None) -> dict:
    """Non-streaming Chat Completions, normalized to the same canonical contract.

    Useful for compatible servers without SSE. Exactly one HTTP request is sent;
    protocol/transport failures are never retried by an adapter.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if request_id:
        headers["Idempotency-Key"] = request_id
    body = {key: value for key, value in payload.items() if key != "stream_options"}
    body["stream"] = False
    response = await client.post(api_base.rstrip("/") + "/chat/completions", headers=headers, json=body)
    if response.status_code >= 400:
        codes = {401: "provider_auth_failed", 402: "provider_balance_insufficient",
                 403: "provider_access_denied", 429: "provider_rate_limited"}
        raise ServiceError(codes.get(response.status_code, f"provider_http_{response.status_code}"),
                           "Athena 暂时无法继续，这件事已保留，请稍后重试。")
    try:
        value = response.json()
        choices = value["choices"]
        if value.get("error") or not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Invalid completion")
        choice = choices[0]
        if choice.get("finish_reason") not in ("stop", "tool_calls"):
            raise ServiceError("generation_incomplete", "这次生成提前结束，未执行尚未完成的操作。可以让我继续。")
        message = choice["message"]
        normalized = {"role": "assistant", "content": "" if message.get("content") is None else message["content"],
                      "reasoning_content": "" if message.get("reasoning_content") is None else message["reasoning_content"]}
        if message.get("tool_calls"):
            normalized["tool_calls"] = message["tool_calls"]
        result = {"message": normalized, "usage": value.get("usage") or {}, "model": value.get("model")}
        from .model_service import validate_result
        validate_result(result)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ServiceError("provider_contract_error", "The model adapter returned an invalid result.") from error
    if normalized["reasoning_content"] and on_reasoning:
        await on_reasoning(normalized["reasoning_content"])
    if normalized["content"]:
        await on_text(normalized["content"])
    return result


async def complete(client: httpx.AsyncClient, api_base: str, api_key: str, payload: dict, on_text: Callable[[str], Awaitable[None]], on_reasoning: Callable[[str], Awaitable[None]] | None = None, *, request_id: str | None = None) -> dict:
    content, reasoning, calls, usage, response_model = "", "", {}, {}, None
    finished = False
    headers = {"Authorization": f"Bearer {api_key}"}
    if request_id:
        headers['Idempotency-Key'] = request_id
    async with client.stream("POST", api_base.rstrip("/") + "/chat/completions", headers=headers, json=payload) as response:
        if response.status_code >= 400:
            await response.aread()
            # Do not forward a provider response containing request details or credentials.
            if response.status_code == 402:
                raise ServiceError("provider_balance_insufficient", "Athena 的服务暂不可用，这件事已保留，请稍后再试。")
            codes = {401: "provider_auth_failed", 403: "provider_access_denied", 429: "provider_rate_limited"}
            raise ServiceError(codes.get(response.status_code, f"provider_http_{response.status_code}"), "Athena 暂时无法继续，这件事已保留，请稍后重试。")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            chunk = json.loads(raw)
            response_model = chunk.get("model") or response_model
            if chunk.get("error"):
                raise ServiceError("provider_stream_error", "模型服务在生成中返回错误，请稍后重试。")
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content += delta["content"]
                    await on_text(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning += delta["reasoning_content"]
                    if on_reasoning:
                        await on_reasoning(delta["reasoning_content"])
                for fragment in delta.get("tool_calls") or []:
                    index = fragment["index"]
                    item = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    item["id"] += fragment.get("id") or ""
                    for key in ("name", "arguments"):
                        item["function"][key] += fragment.get("function", {}).get(key) or ""
                if choice.get("finish_reason"):
                    if choice["finish_reason"] not in ("stop", "tool_calls"):
                        raise ServiceError("generation_incomplete", "这次生成提前结束，未执行尚未完成的操作。可以让我继续。")
                    finished = True
    if not finished:
        raise ServiceError("stream_interrupted", "模型连接中断，已保留生成内容。可以让我继续。")
    result = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if calls:
        result["tool_calls"] = [calls[index] for index in sorted(calls)]
    return {"message": result, "usage": usage, "model": response_model}
