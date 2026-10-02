import json

import pytest

from athena.metrics import summarize, usage_metrics


def request(usage, **changes):
    return {"id": "request-one", "model": "deepseek-flash", "status": "complete",
            "usage": usage, "created_at": "2026-10-03T00:00:00Z", "finished_at": "2026-10-03T00:00:01Z",
            **changes}


KNOWN = {"prompt_tokens": 100, "completion_tokens": 10,
         "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}


@pytest.mark.parametrize("value", [None, "", "null", "[]", "[1]", '"usage"', "17", "true", "{invalid", 12, [1]])
def test_non_object_or_malformed_usage_remains_unknown(value):
    item = usage_metrics(request(value))
    for key in ("input_tokens", "output_tokens", "cache_hit_tokens", "cache_miss_tokens", "cache_rate", "cost_usd_low"):
        assert item[key] is None
    summary = summarize([request(value)])["total"]
    assert summary["unreported_requests"] == 1 and summary["unpriced_requests"] == 1
    assert summary["cost_usd_low"] is None


@pytest.mark.parametrize("details", [None, [], [1], "unknown", 5, True])
def test_invalid_cache_details_do_not_discard_valid_provider_totals(details):
    item = usage_metrics(request(json.dumps({"prompt_tokens": 100, "completion_tokens": 10,
                                             "prompt_tokens_details": details})))
    assert item["input_tokens"] == 100 and item["output_tokens"] == 10
    assert item["cache_hit_tokens"] is None and item["cache_rate"] is None
    assert item["cost_usd_low"] is None


@pytest.mark.parametrize("profile", ["null", "[]", "[1]", '"provider"', "true", "{invalid", "{}",
    json.dumps({"api_base": None}), json.dumps({"api_base": ["https://api.deepseek.com"]}),
    json.dumps({"api_base": 1}), json.dumps({"api_base": "https://different-provider.invalid/v1"})])
def test_invalid_or_other_provider_snapshot_cannot_claim_official_pricing(profile):
    item = usage_metrics(request(json.dumps(KNOWN), profile_snapshot=profile))
    assert item["input_tokens"] == 100 and item["cache_rate"] == 0.4
    assert item["cost_usd_low"] is None and item["cost_usd_high"] is None


@pytest.mark.parametrize("invalid", [-1, True, "100", 12.5, 10**400])
def test_invalid_numeric_fields_are_unknown_and_do_not_overflow(invalid):
    item = usage_metrics(request(json.dumps({field: invalid for field in KNOWN})))
    assert item["input_tokens"] is None and item["output_tokens"] is None
    assert item["cache_rate"] is None and item["cost_usd_low"] is None


def test_valid_nested_cache_and_mixed_unknown_rows_still_aggregate():
    good = request(json.dumps({"prompt_tokens": 100, "completion_tokens": 10,
                               "prompt_tokens_details": {"cached_tokens": 40}}),
                   profile_snapshot=json.dumps({"api_base": "https://api.deepseek.com/v1/"}))
    rows = [good, request("null", id="null-usage"), request(json.dumps(KNOWN), id="bad-profile", profile_snapshot="[]")]
    summary = summarize(rows)["total"]
    assert summary["request_count"] == 3
    assert summary["input_tokens"] == 200 and summary["output_tokens"] == 20
    assert summary["cache_rate"] == 0.4
    assert summary["unreported_requests"] == 1 and summary["unpriced_requests"] == 2
    assert summary["cost_usd_low"] == pytest.approx((40 * .003 + 60 * .15 + 10 * .6) / 1_000_000)
    assert usage_metrics(request(json.dumps(KNOWN)))["cost_usd_low"] == summary["cost_usd_low"]
