"""Hide flagd from the agent's kubectl tools.

Astronomy Shop faults are injected by flipping a flag in the flagd-config ConfigMap and restarting flagd:
the ConfigMap names the answer and the restart (young pod, rollout events) tells fault from no-fault.
The agent must localize the faulty microservice, so flagd does not exist as far as it can tell.
"""
import json

import yaml
from mcp.types import CallToolResult, TextContent

HIDDEN = "flagd"


def _is_hidden(item: dict) -> bool:
    """A list entry is hidden when the resource (or the event's object) is named after flagd."""
    names = (item.get("name"), (item.get("metadata") or {}).get("name"), (item.get("involvedObject") or {}).get("name"))
    return any(HIDDEN in str(n).lower() for n in names if n)


def scrub(text: str) -> str:
    """Drop flagd from kubectl output: whole entries from JSON/YAML lists, lines from anything else."""
    if HIDDEN not in text.lower():
        return text
    try:
        data = yaml.safe_load(text)  # JSON is YAML too
    except yaml.YAMLError:
        data = None
    if isinstance(data, dict):
        for key in ("items", "events"):
            if isinstance(data.get(key), list):
                data[key] = [x for x in data[key] if not (isinstance(x, dict) and _is_hidden(x))]
                return json.dumps(data, indent=2, default=str)
    return "\n".join(line for line in text.splitlines() if HIDDEN not in line.lower())


async def hide_flagd(request, handler):
    """MCP tool interceptor: calls naming flagd look like missing resources, outputs come back without flagd."""
    if request.server_name != "kubernetes":
        return await handler(request)
    if HIDDEN in json.dumps(request.args).lower():
        target = "/".join(filter(None, (request.args.get("resourceType"), request.args.get("name"))))
        text = json.dumps({"error": f"Resource {target} not found", "status": "not_found"}, indent=2)
        return CallToolResult(content=[TextContent(type="text", text=text)])
    result = await handler(request)
    for content in result.content:
        if isinstance(content, TextContent):
            content.text = scrub(content.text)
    return result


if __name__ == "__main__":
    import asyncio
    from types import SimpleNamespace

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
    print("hide_flagd: ok")
