# Run from sre-agent/: PYTHONPATH=tools python tests/test_hide_flagd.py  (no cluster needed;
# importing the tools package would connect to the MCP servers)
import asyncio
import json
from types import SimpleNamespace

import yaml
from hide_flagd import hide_flagd, scrub

# list summary (default json output): the flagd pod disappears, the others are untouched
pods = json.dumps({"items": [{"name": "ad-1", "createdAt": "t0"}, {"name": "flagd-2", "createdAt": "t1"}]})
assert json.loads(scrub(pods))["items"] == [{"name": "ad-1", "createdAt": "t0"}]
# raw yaml list of events, still yaml: the flagd rollout and shipping's init container (by fieldPath) are gone
events = ("apiVersion: v1\nkind: List\nitems:\n"
          "- metadata: {name: flagd-7c.1}\n  involvedObject: {name: flagd}\n  reason: ScalingReplicaSet\n"
          "- involvedObject: {name: shipping-1, fieldPath: 'spec.initContainers{wait-for-flagd}'}\n  reason: Pulled\n"
          "- metadata: {name: ad-1.2}\n  involvedObject: {name: ad-1}\n  reason: Started\n")
assert scrub(events).startswith("apiVersion: v1\nkind: List\n")
assert [e["reason"] for e in yaml.safe_load(scrub(events))["items"]] == ["Started"]
# summary json of events (the tool's default): the ones about wait-for-flagd go
summary = json.dumps({"events": [{"reason": "Started", "message": "Started container wait-for-flagd", "involvedObject": {"name": "shipping-1"}},
                                 {"reason": "Started", "message": "Started container shipping", "involvedObject": {"name": "shipping-1"}}]})
assert [e["message"] for e in json.loads(scrub(summary))["events"]] == ["Started container shipping"]
# one object as yaml (shipping pod, demo 3.1.0 chart): FLAGD_* env vars and the wait-for-flagd init container go,
# with the initContainers/initContainerStatuses they leave empty; the rest is untouched
pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "shipping-1"},
       "spec": {"initContainers": [{"command": ["sh", "-c", "until nc -z -v -w30 flagd 8013; do echo waiting for flagd; sleep 2; done;"],
                                    "image": "busybox:latest", "name": "wait-for-flagd"}],
                "containers": [{"name": "shipping", "env": [{"name": "OTEL_SERVICE_NAME", "value": "shipping"},
                                                            {"name": "FLAGD_HOST", "value": "flagd"}, {"name": "FLAGD_PORT", "value": "8013"}]}]},
       "status": {"initContainerStatuses": [{"name": "wait-for-flagd", "ready": True}], "containerStatuses": [{"name": "shipping", "ready": True}]}}
assert yaml.safe_load(scrub(yaml.safe_dump(pod))) == {
    "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "shipping-1"},
    "spec": {"containers": [{"name": "shipping", "env": [{"name": "OTEL_SERVICE_NAME", "value": "shipping"}]}]},
    "status": {"containerStatuses": [{"name": "shipping", "ready": True}]}}
# tables and describe output: flagd rows are dropped
assert scrub("NAME   AGE\nad-1   2d\nflagd-2   30s") == "NAME   AGE\nad-1   2d"
# describe of the same pod: the init container block and its now empty header go, the env lines and its event too
describe = """Name:             shipping-1
Init Containers:
  wait-for-flagd:
    Image:         busybox:latest
    Command:
      sh
      -c
      until nc -z -v -w30 flagd 8013; do echo waiting for flagd; sleep 2; done;
    State:          Terminated
      Reason:       Completed
Containers:
  shipping:
    Environment:
      OTEL_SERVICE_NAME:  shipping
      FLAGD_HOST:         flagd
      FLAGD_PORT:         8013
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access (ro)
Events:
  Type    Reason   Age  From     Message
  ----    ------   ---  ----     -------
  Normal  Started  5m   kubelet  Started container wait-for-flagd
  Normal  Started  5m   kubelet  Started container shipping"""
assert scrub(describe) == """Name:             shipping-1
Containers:
  shipping:
    Environment:
      OTEL_SERVICE_NAME:  shipping
    Mounts:
      /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access (ro)
Events:
  Type    Reason   Age  From     Message
  ----    ------   ---  ----     -------
  Normal  Started  5m   kubelet  Started container shipping"""
# a header keeping other children stays
assert scrub("Init Containers:\n  wait-for-flagd:\n    Image: busybox\n  wait-for-kafka:\n    Image: busybox") == \
    "Init Containers:\n  wait-for-kafka:\n    Image: busybox"
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
