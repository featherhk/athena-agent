import json
from typing import Awaitable, Callable
import httpx
from .errors import ServiceError


async def complete(client: httpx.AsyncClient, api_base: str, api_key: str, payload: dict, on_text: Callable[[str], Awaitable[None]]) -> dict:
    content, reasoning, calls, usage, response_model = "", "", {}, {}, None
    finished = False
    async with client.stream("POST", api_base.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json=payload) as response:
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
                reasoning += delta.get("reasoning_content") or ""
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
