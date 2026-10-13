# Run from sre-agent/: PYTHONPATH=. poetry run python tests/test_evaluation_localization.py
from evaluation.evaluation import blamed_harness, evaluate_localization as loc

pay = {"target": "payment", "accepted_targets": ["payment"]}
assert loc(pay, "payment")
assert loc(pay, "Deployment/payment, ")
assert loc(pay, "astronomy-shop/pod/payment-7d9f8b6c5-abcde")
assert loc(pay, "payment-service")
assert not loc(pay, "payment, checkout")  # over-listing is not free
assert not loc(pay, "flagd")  # the harness is never accepted
assert not loc({"target": "ad", "accepted_targets": ["ad"]}, "load-generator")  # no substring hit
assert not loc({"target": "cart", "accepted_targets": ["cart"]}, "valkey-cart")
kafka = {"target": "kafka", "accepted_targets": ["kafka", "checkout", "fraud-detection"]}
assert loc(kafka, "checkout") and loc(kafka, "kafka, fraud-detection") and not loc(kafka, "kafka, payment")
assert loc({"target": "geo"}, "geo-99d8c58c-lswlc") and not loc({"target": "geo"}, "mongodb-geo")  # default [target]
assert loc({"target": None}, "")
assert blamed_harness("payment, flagd") and blamed_harness("flagd-ui") and not blamed_harness("payment")

assert loc(pay, "payment, checkout", any_of=True) and not loc(pay, "checkout", any_of=True)
assert not loc({"target": "frontend", "accepted_targets": ["frontend"]}, "frontend-proxy", any_of=True)

# Formats from real Hotel/Social runs that the first normalizer rejected
user = {"target": "user", "accepted_targets": ["user"]}
geo = {"target": "geo", "accepted_targets": ["geo", "mongodb-geo"]}
rate = {"target": "rate", "accepted_targets": ["rate", "mongodb-rate"]}
assert loc(user, "pod:user-8477d787d8-2nhtl") and loc(user, "user Deployment") and loc(user, "ReplicaSet/user-8477d787d8")
assert loc(user, "pod/user-8477d787d8-9657r (container: hotel-reserv-user), ReplicaSet/user-8477d787d8 pod-template")
assert loc(geo, "hotel-reserv-geo container in pod geo-6b4b89b5f5-hlpqn (application config: wrong port)")
assert loc(geo, "pod/geo-99d8c58c-lswlc:hotel-reserv-geo") and loc(geo, "replicaset/geo-6b4b89b5f5")
assert loc(geo, "mongodb-geo (MongoDB instance) — MongoDB user/role for geo service lacks privileges on 'geo-db'")
assert loc(rate, "rate-c9bc58c85-twl27 (pod) - container hotel-reserv-rate") and loc(rate, "replicaset/rate-c9bc58c85")
assert loc(rate, "pod/rate-c9bc58c85-twl27 (container: hotel-reserv-rate), hotel-reserv-rate (Deployment/Service) — auth fails")
assert loc(rate, "pod/mongodb-rate-56cc8659c9-x2k9p") and not loc({"target": "rate", "accepted_targets": ["rate"]}, "mongodb-rate")

# Astronomy Shop: legacy (pre-2.0) names, annotations with commas, multi-word names
assert loc(pay, "payment (Deployment, namespace: astronomy-shop)")
assert loc({"target": "product-catalog", "accepted_targets": ["product-catalog"]}, "productcatalogservice")
assert loc({"target": "product-catalog", "accepted_targets": ["product-catalog"]}, "Product Catalog service")
assert loc({"target": "ad", "accepted_targets": ["ad"]}, "AdService") and loc(kafka, "frauddetectionservice")
assert not loc({"target": "frontend", "accepted_targets": ["frontend"]}, "Frontend Proxy")

from evaluation.evaluation import normalize_service as ns
assert ns("hotel-reserv-geo") == "geo" and ns("user-8477d787d8-frxzx (container: hotel-reserv-user)") == "user"
assert ns("pod/url-shorten-mongodb-5b6fdb4d8b-b55c8") == "url-shorten-mongodb" and ns("Payment-Service") == "payment"
assert ns("geo-5f7b9-hlpqn") == "geo" and ns("otel-collector-agent-x7k2p") == "otel-collector-agent"  # 5-char hash, DaemonSet pod
assert ns("geo-pvc") == "geo-pvc" and ns("Secret/mongodb-tls") == "mongodb-tls" and ns("astronomy-db") == "astronomy-db"
