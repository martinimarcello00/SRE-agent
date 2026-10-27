# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_openai_key_fallback.py
# No API key needed: fake keys, `.env` is not loaded, the usage API and the OpenAI call are replaced.
import importlib
import os
import sys
from types import ModuleType

import httpx
import openai
from langchain_openai import ChatOpenAI

fake_dotenv = ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda *_, **__: None  # the real .env would put real keys and limits in the chain
sys.modules["dotenv"] = fake_dotenv

KEYS = {"OPENAI_API_KEY": "k1", "OPENAI_ADMIN_API_KEY": "a1", "OPENAI_API_KEY_FALLBACK": "k2",
        "OPENAI_ADMIN_API_KEY_FALLBACK": "a2", "OPENAI_API_KEY_FALLBACK_2": "k3", "OPENAI_ADMIN_API_KEY_FALLBACK_2": "a3"}
usage = {}  # gpt-5-mini tokens used today, by admin key


def load_settings(**env):
    for name in [*KEYS, "MAX_DAILY_OPENAI_TOKEN_LIMIT", "MAX_DAILY_OPENAI_TOKEN_LIMIT_FALLBACK_2"]:
        os.environ.pop(name, None)
    os.environ.update(MAX_DAILY_OPENAI_TOKEN_LIMIT="2000000", **env)
    import config.settings
    settings = importlib.reload(config.settings)
    settings.get_today_model_usage = lambda model_name, admin_api_key: {"total_tokens": usage[admin_api_key]}
    usage.update(a1=0, a2=0, a3=0)
    return settings


def active(settings):
    return os.environ["OPENAI_API_KEY"], os.environ["OPENAI_ADMIN_API_KEY"], settings.GPT5_MINI.openai_api_key.get_secret_value()


# priority order by today's usage: 1 -> 2 -> 3 (whose limit is 3M, not 2M), none left -> False and nothing changes
s = load_settings(**KEYS)
assert s.select_openai_key() and active(s) == ("k1", "a1", "k1")
usage["a1"] = 2_000_000
assert s.select_openai_key() and active(s) == ("k2", "a2", "k2")
usage.update(a2=2_000_000, a3=2_500_000)
assert s.select_openai_key() and active(s) == ("k3", "a3", "k3")
usage["a3"] = 3_000_000
assert not s.select_openai_key() and active(s) == ("k3", "a3", "k3")

# after 00:00 UTC the usage restarts: back to key 1
usage.update(a1=0, a2=0, a3=0)
assert s.select_openai_key() and active(s) == ("k1", "a1", "k1")

# a pair without its admin key is never used (its usage cannot be read)
s = load_settings(**{k: v for k, v in KEYS.items() if k != "OPENAI_ADMIN_API_KEY_FALLBACK_2"})
usage.update(a1=2_000_000, a2=2_000_000)
assert not s.select_openai_key() and active(s)[0] == "k1"


# insufficient_quota mid-run on key 1: retry on key 2, and key 1 stays out for the rest of the execution
# (out of credit does not reset at 00:00 UTC), even with no usage today
def quota_error():
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return openai.RateLimitError("quota", response=response, body={"code": "insufficient_quota"})


other_worker = False


def fake_generate(self, *_, **__):
    key = self.openai_api_key.get_secret_value()
    if key == "k1":
        if other_worker:  # a parallel worker hits the same error and switches while this call is in flight
            s.select_openai_key(0)
        raise quota_error()
    return key


ChatOpenAI._generate = fake_generate
s = load_settings(**KEYS)
assert s.GPT5_MINI._generate([]) == "k2" and active(s)[0] == "k2"
assert s.select_openai_key() and active(s)[0] == "k2"

# two parallel workers: the second one retries on key 2, it does not switch again to key 3
other_worker, s = True, load_settings(**KEYS)
assert s.GPT5_MINI._generate([]) == "k2" and active(s)[0] == "k2"
print("ok")
