# Run from sre-agent/: PYTHONPATH=tools python tests/test_hide_flagd.py  (no cluster needed;
# importing the tools package would connect to the MCP servers)
import asyncio
import json
from types import SimpleNamespace

from hide_flagd import hide_flagd, scrub

# list summary (default json output): the flagd pod disappears, the others are untouched
pods = json.dumps({"items": [{"name": "ad-1", "createdAt": "t0"}, {"name": "flagd-2", "createdAt": "t1"}]})
assert json.loads(scrub(pods))["items"] == [{"name": "ad-1", "createdAt": "t0"}]
# raw yaml list of events: the flagd rollout is gone
events = "kind: List\nitems:\n- metadata: {name: flagd-7c.1}\n  involvedObject: {name: flagd}\n  reason: ScalingReplicaSet\n- metadata: {name: ad-1.2}\n  involvedObject: {name: ad-1}\n  reason: Started\n"
assert [e["reason"] for e in json.loads(scrub(events))["items"]] == ["Started"]
# tables and describe output: flagd rows are dropped
assert scrub("NAME   AGE\nad-1   2d\nflagd-2   30s") == "NAME   AGE\nad-1   2d"
# no flagd, no change (Hotel Reservation / Social Network outputs stay byte-identical)
assert scrub("NAME   AGE\nfrontend-1   2d") == "NAME   AGE\nfrontend-1   2d"


async def server(_):
    raise AssertionError("a call naming flagd must not reach the cluster")


def call(server_name, **args):
    return asyncio.run(hide_flagd(SimpleNamespace(server_name=server_name, args=args), server))


blocked = call("kubernetes", resourceType="configmaps", name="flagd-config", namespace="astronomy-shop")
assert json.loads(blocked.content[0].text) == {"error": "Resource configmaps/flagd-config not found", "status": "not_found"}
assert "not_found" in call("kubernetes", resourceType="pods", labelSelector="app.kubernetes.io/name=flagd").content[0].text
print("ok")
