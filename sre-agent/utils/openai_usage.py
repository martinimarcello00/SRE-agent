"""Utilities for querying OpenAI usage metrics."""
from __future__ import annotations

import datetime
import logging
import os
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


def get_today_completions_usage(
    bucket_width: str = "1d",
    raw_output: Optional[bool] = False,
    by_model: bool = False,
) -> Dict[str, int] | Dict[str, Dict[str, int]]:
    """Return today's organization usage for the completions endpoint.

    Args:
        bucket_width: Width of the aggregation bucket passed to the API (e.g., "1d", "1h").
        raw_output: When True, return the raw JSON response from the API.
        by_model: When True, return usage grouped by model (dict keyed by model name).

    Returns:
        - Default: Dict with ``input_tokens``, ``output_tokens`` and ``total_tokens``.
        - If ``by_model=True``: Dict[str, Dict[str, int]] mapping model -> token breakdown.
    """
    api_key = os.getenv("OPENAI_ADMIN_API_KEY")

    if not api_key:
        logger.error("OPENAI_ADMIN_API_KEY environment variable is missing.")

    start_dt = datetime.datetime.now(datetime.UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_dt = start_dt + datetime.timedelta(days=1)

    params: list[tuple[str, int | str]] = [
        ("start_time", int(start_dt.timestamp())),
        ("end_time", int(end_dt.timestamp())),
        ("bucket_width", bucket_width),
    ]

    # Request grouping by model when desired (API may aggregate per-model results)
    if by_model:
        params.append(("group_by", "model"))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.get(
        "https://api.openai.com/v1/organization/usage/completions",
        params=params,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()
    json_response = response.json()

    if raw_output:
        return json_response

    # Defensive parsing of results
    data = json_response.get("data", [])
    if not data:
        # No data present
        if by_model:
            return {}
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    results = data[0].get("results", [])

    if by_model:
        # Group usage by model (sum across result buckets if multiple)
        usage_by_model: Dict[str, Dict[str, int]] = {}
        for r in results:
            model_name = (
                r.get("model")
                or r.get("name")
                or r.get("model_name")
                or "unknown"
            )
            in_toks = int(r.get("input_tokens", 0) or 0)
            out_toks = int(r.get("output_tokens", 0) or 0)
            if model_name not in usage_by_model:
                usage_by_model[model_name] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
            usage_by_model[model_name]["input_tokens"] += in_toks
            usage_by_model[model_name]["output_tokens"] += out_toks
            usage_by_model[model_name]["total_tokens"] += in_toks + out_toks
        return usage_by_model

    # Default behavior: return aggregate figures if available
    if len(results) > 0:
        input_tokens = int(results[0].get("input_tokens", 0) or 0)
        output_tokens = int(results[0].get("output_tokens", 0) or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    # If no results, return zero usage
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def get_today_model_usage(model_name: str, bucket_width: str = "1d") -> Dict[str, int]:
    """Return today's token usage for a specific model.

    Performs a per-model aggregation using ``get_today_completions_usage`` with
    ``by_model=True`` and returns a zeroed usage dict if the model is not found
    or the API provides no data.

    Args:
        model_name: Exact model identifier to look up (e.g., "gpt-5-mini").
        bucket_width: Aggregation bucket width (default "1d").

    Returns:
        Dict with keys ``input_tokens``, ``output_tokens``, ``total_tokens``.
    """
    try:
        usage = get_today_completions_usage(bucket_width=bucket_width, by_model=True)
    except Exception as exc:
        logger.error("Failed to retrieve usage data: %s", exc)
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    if not isinstance(usage, dict):  # Defensive: unexpected type
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # Allow partial matching: e.g. "gpt-5-mini" matches "gpt-5-mini-2025-08-07"
    # Aggregate across all matching keys (case-insensitive substring containment)
    target_lower = model_name.lower()
    aggregate = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for key, stats in usage.items():
        if not isinstance(stats, dict):
            continue
        if target_lower in key.lower():
            aggregate["input_tokens"] += int(stats.get("input_tokens", 0) or 0)
            aggregate["output_tokens"] += int(stats.get("output_tokens", 0) or 0)
            aggregate["total_tokens"] += int(stats.get("total_tokens", 0) or 0)

    # If nothing matched, return zeros
    return aggregate

