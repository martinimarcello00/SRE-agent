"""Utilities for querying OpenAI usage metrics."""
from __future__ import annotations

import datetime
import logging
import os
from typing import Dict

import requests

logger = logging.getLogger(__name__)


def get_today_completions_usage(bucket_width: str = "1d") -> Dict[str, int]:
    """Return today's organization usage for the completions endpoint.

    Args:
        bucket_width: Width of the aggregation bucket passed to the API.

    Returns:
        Dictionary containing ``input_tokens``, ``output_tokens`` and ``total_tokens``.
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

    # Check if there are any results in the API response
    if len(json_response["data"][0]["results"]) > 0:
        # Extract input and output tokens from the first result
        input_tokens = json_response["data"][0]["results"][0]["input_tokens"]
        output_tokens = json_response["data"][0]["results"][0]["output_tokens"]

        # Return a dictionary with token usage
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    else:
        # If no results, return zero usage
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
