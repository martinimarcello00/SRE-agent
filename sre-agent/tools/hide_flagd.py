"""Hide flagd from the agent's kubectl tools.

Astronomy Shop faults are injected by flipping a flag in the flagd-config ConfigMap and restarting flagd:
the ConfigMap names the answer and the restart (young pod, rollout events) tells fault from no-fault.
The agent must localize the faulty microservice, so flagd does not exist as far as it can tell.
"""
import json

import yaml
from mcp.types import CallToolResult, TextContent

HIDDEN = "flagd"


def _names_flagd(value) -> bool:
    return HIDDEN in str(value).lower()


def _about_flagd(entry) -> bool:
    """A list entry is about flagd when one of its own fields, its name or its event's object names it: the flagd
    pod/rollout, a FLAGD_* env var, shipping's wait-for-flagd init container and the events about it."""
    if isinstance(entry, list):
        return False
    if not isinstance(entry, dict):
        return _names_flagd(entry)
    own = [v for v in entry.values() if not isinstance(v, (dict, list))]
    return _names_flagd(own + [(entry.get("metadata") or {}).get("name"), entry.get("involvedObject")])


def _prune(data):
    """Drop flagd at any depth: list entries about it, fields naming it, and the lists/maps this leaves empty."""
    if isinstance(data, list):
        return [_prune(x) for x in data if not _about_flagd(x)]
    if isinstance(data, dict):
        kept = {k: _prune(v) for k, v in data.items()
                if not _names_flagd(k) and (isinstance(v, (dict, list)) or not _names_flagd(v))}
        return {k: v for k, v in kept.items() if v or not data[k]}
    return data


def _drop_lines(text: str) -> str:
    """Drop every line naming flagd with the block indented under it (describe's wait-for-flagd init container),
    and the header this leaves empty (its "Init Containers:")."""
    kept, cut = [], None  # [indent, line, lost its first child]
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if cut is not None and indent > cut:
            continue
        cut = None
        if not _names_flagd(line):
            kept.append([indent, line, False])
            continue
        cut = indent
        if kept and kept[-1][1].rstrip().endswith(":") and kept[-1][0] < indent:
            kept[-1][2] = True
    following = [k[0] for k in kept[1:]] + [-1]
    return "\n".join(line for (indent, line, emptied), nxt in zip(kept, following) if not (emptied and nxt <= indent))


def scrub(text: str) -> str:
    """Drop flagd from kubectl output in the format it came: JSON and -o yaml as data, tables and describe by lines."""
    if HIDDEN not in text.lower():
        return text
    try:
        data = yaml.safe_load(text)  # JSON is YAML too
    except yaml.YAMLError:
        data = None
    if isinstance(data, (dict, list)) and text.lstrip()[0] in "{[":
        return json.dumps(_prune(data), indent=2, default=str)
    if isinstance(data, dict) and "apiVersion" in data:
        return yaml.safe_dump(_prune(data), sort_keys=False)
    return _drop_lines(text)


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

