"""Usage comes from the provider. Cost is an explicit estimate, not a bill."""
import json

PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICING_DATE = "2026-10-03"


def _object(value):
    """Provider metadata is optional and must not take down the usage endpoint."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def usage_metrics(row):
    raw = _object(row.get("usage"))
    def number(value):
        # Counts outside the client's exact integer range are invalid telemetry.
        return value if type(value) is int and 0 <= value <= 2**53 - 1 else None
    prompt = number(raw.get("prompt_tokens"))
    completion = number(raw.get("completion_tokens"))
    hit = number(raw.get("prompt_cache_hit_tokens"))
    miss = number(raw.get("prompt_cache_miss_tokens"))
    if hit is None:
        details = raw.get("prompt_tokens_details")
        hit = number(details.get("cached_tokens")) if isinstance(details, dict) else None
    if miss is None and prompt is not None and hit is not None and hit <= prompt:
        miss = prompt - hit
    if prompt is not None and hit is not None and (hit > prompt or miss is not None and hit + miss != prompt):
        hit = miss = None
    cost = None
    saved_profile = row.get("profile_snapshot")
    profile = _object(saved_profile)
    # Rows predating profile snapshots used the original official endpoint.
    # A present but malformed snapshot supplies no trustworthy pricing source.
    api_base = profile.get("api_base") if saved_profile else "https://api.deepseek.com"
    official = isinstance(api_base, str) and api_base.rstrip("/") in ("https://api.deepseek.com", "https://api.deepseek.com/v1")
    if official and row["model"] == "deepseek-flash" and None not in (hit, miss, completion):
        cost = (hit * .003 + miss * .15 + completion * .6) / 1_000_000
    return {"id": row["id"], "model": row["model"], "status": row["status"], "input_tokens": prompt,
            "output_tokens": completion, "cache_hit_tokens": hit, "cache_miss_tokens": miss,
            "cache_rate": hit / (hit + miss) if hit is not None and miss is not None and hit + miss > 0 else None,
            "cost_usd_low": cost, "cost_usd_high": cost * 2 if cost is not None else None,
            "created_at": row["created_at"], "finished_at": row["finished_at"],
            "error_code": row.get("error_code"), "response_model": row.get("response_model"), "context_epoch": row.get("context_epoch")}


def summarize(rows):
    requests = [usage_metrics(row) for row in rows]
    hit = sum(r["cache_hit_tokens"] or 0 for r in requests if r["cache_miss_tokens"] is not None)
    miss = sum(r["cache_miss_tokens"] or 0 for r in requests if r["cache_hit_tokens"] is not None)
    known = [r for r in requests if r["cost_usd_low"] is not None]
    return {"requests": requests[-30:][::-1], "total": {
        "request_count": len(requests), "input_tokens": sum(r["input_tokens"] or 0 for r in requests),
        "output_tokens": sum(r["output_tokens"] or 0 for r in requests),
        "cache_hit_tokens": hit, "cache_miss_tokens": miss,
        "cache_rate": hit / (hit+miss) if hit+miss else None,
        "cache_unreported_requests": sum(r["cache_hit_tokens"] is None or r["cache_miss_tokens"] is None for r in requests),
        "cost_usd_low": sum(r["cost_usd_low"] for r in known) if known else None,
        "cost_usd_high": sum(r["cost_usd_high"] for r in known) if known else None,
        "unreported_requests": sum(r["input_tokens"] is None for r in requests),
        "unpriced_requests": len(requests)-len(known)},
        "pricing": {"currency": "USD", "source": PRICING_URL, "checked_at": PRICING_DATE, "kind": "off_peak_to_peak_estimate"}}
