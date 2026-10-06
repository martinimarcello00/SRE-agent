from models import EvaluationResult
from prompts import EVALUATION_PROMPT
from config import GPT5_1
from utils import get_today_model_usage
import logging
import re
from typing import Optional

GPT5_1_NAME = "gpt-5.1"  # substring of the dated name the usage API reports (gpt-5.1-2025-11-13)
GPT5_1_TOKEN_DAILY_LIMIT = 240_000
NO_ANALYSIS = "No analysis data available"  # literal written by agents/supervisor_agent.py when triage finds no symptoms

logger = logging.getLogger(__name__)

def evaluate_detection(fault_scenario: dict, detection: bool)->bool:
    """
    Evaluates whether the detection result matches the ground truth for the fault scenario.

    Args:
        fault_scenario (dict): The fault scenario dictionary, expected to contain a "target" key.
        detection (bool): The detection result to evaluate.

    Returns:
        bool: True if the detection matches the ground truth, False otherwise.
    """
    target = fault_scenario.get("target", None)
    gt_detection = True if target else False
    return gt_detection == detection

# Fault-injection machinery (flagd feature flags, Chaos Mesh): never a valid faulty service.
HARNESS = {"flagd", "flagd-ui", "flag", "chaos-mesh"}

# Characters Kubernetes uses for the ReplicaSet hash in pod names (rand.SafeEncodeString): no vowels
# and no 0/1/3, so a real name segment such as "-proxy" or "-mongodb" is never mistaken for a hash.
K8S = "bcdfghjklmnpqrstvwxz2456789"
# Trailing "-<5..10 hash chars>" (ReplicaSet) plus an optional "-<5 chars>" (pod):
# "geo-6b4b89b5f5-hlpqn" -> "geo", "geo-6b4b89b5f5" -> "geo", "otel-collector-agent-x7k2p" -> "otel-collector-agent".
# ponytail: hashes shorter than 5 chars (1 pod in ~430k) are not stripped; a bound below 4 would cut
# real suffixes ("geo-pvc" -> "geo", "mongodb-tls" -> "mongodb", "astronomy-db" -> "astronomy").
POD_HASH = re.compile(rf"-[{K8S}]{{5,10}}(?:-[a-z0-9]{{5}})?$")
# Leading "<kind>:" written with a colon, singular or plural: "Pod: x", "deployment:x", "pods: x" -> "x".
# The "kind/x" form is handled later by keeping the last "/" segment.
KIND = re.compile(r"^(?:pod|deployment|service|svc|replicaset|statefulset|daemonset|container|namespace"
                  r"|persistentvolumeclaim|pvc|secret|configmap|endpoints?)s?:\s*")
# Application prefixes in front of the service name: Hotel container names ("hotel-reserv-geo") and
# Astronomy Shop names prefixed with the Helm release ("astronomy-shop-adservice", "opentelemetry-demo-ad").
PREFIX = re.compile(r"^(?:hotel-reserv|astronomy-shop|opentelemetry-demo|otel-demo)-")
# Generic words dropped from multi-word answers: "the Product Catalog service" -> "product-catalog".
FILLER = {"the", "a", "an", "service", "microservice", "svc", "deployment", "pod", "pods", "container",
          "app", "application", "component", "broker", "instance", "workload"}

def normalize_service(name: str) -> str:
    """'Deployment/Payment-Service', 'pod:payment-7d9f8b6c5-x2k9p', 'Product Catalog service' -> service name."""
    s = re.split(r"\s[—–-]\s|\s*\(", name.strip().lower(), maxsplit=1)[0]  # cut "(annotation" / " — free text"
    s = KIND.sub("", s)  # "pod:user-8477d787d8-2nhtl" -> "user-8477d787d8-2nhtl"
    words = s.split()
    if len(words) > 1 and all(w.isalpha() for w in words):  # "Product Catalog service" -> product-catalog
        s = "-".join(w for w in words if w not in FILLER)
    else:  # "hotel-reserv-geo container in pod geo-..." -> first token
        s = (words or [""])[0]
    s = s.rsplit("/", 1)[-1].split(":", 1)[0]  # "ns/pod/x" -> x, "pod/x:container" -> x, "x:50051" -> x
    s = re.sub(r"^(?:oteldemo|hipstershop)\.", "", s).split(".", 1)[0]  # "oteldemo.AdService", "x.ns.svc"
    s = PREFIX.sub("", POD_HASH.sub("", s))  # "hotel-reserv-geo-6b4b89b5f5-hlpqn" -> "geo"
    return re.sub(r"[-_]?(?:service|svc)$", "", s).replace("_", "-")  # "payment-service", "adservice" -> drop suffix

def localized_services(localization: str) -> set[str]:
    return {normalize_service(x) for x in re.split(r",(?![^()]*\))", localization) if x.strip()}  # commas outside ()

def blamed_harness(localization: str) -> bool:
    return bool(localized_services(localization) & HARNESS)

def evaluate_localization(fault_scenario: dict, localization: str, any_of: bool = False) -> bool:
    """
    Evaluates whether the localization result matches the ground truth target in the fault scenario.

    Args:
        fault_scenario (dict): The fault scenario dictionary, expected to contain a "target" key.
        localization (str): The localization result to evaluate.
        any_of (bool): True if at least one blamed service must be in accepted_targets (default [target]);
            False (default) if every blamed service must be.

    Returns:
        bool: True if the localization matches the ground truth, False otherwise.
    """
    target = fault_scenario.get("target", None)

    # If no target is defined (no fault scenario), return True if localization is also None/empty
    if target is None:
        return localization is None or localization == ''

    # If localization is None or not a string, cannot match
    if not isinstance(localization, str):
        return False

    # Exact match on normalized names, compared without hyphens (fraud-detection == frauddetectionservice).
    # accepted_targets defaults to [target]; harness components such as flagd are never accepted.
    accepted = fault_scenario.get("accepted_targets") or [target]
    found = {s.replace("-", "") for s in localized_services(localization)}
    ok = {normalize_service(a).replace("-", "") for a in accepted}
    return bool(found & ok) if any_of else bool(found) and found <= ok

def evaluate_rca_analysis(fault_scenario: dict, rca_analysis: str, langsmith_metadata: Optional[dict] = None) -> tuple[Optional[int], str]:
    """
    Evaluates the root cause analysis (RCA) result using an LLM and returns a score and explanation.

    Args:
        fault_scenario (dict): The fault scenario dictionary, expected to contain an "RCA_gt" key.
        rca_analysis (str): The RCA analysis result to evaluate.

    Returns:
        tuple[Optional[int], str]: A tuple containing the evaluation score (or None on error) and an explanation string.
    """
    if not rca_analysis.strip() or rca_analysis.strip() == NO_ANALYSIS:
        return 1, "No analysis was produced; judge not called."

    token_usage = get_today_model_usage(model_name=GPT5_1_NAME)

    if token_usage["total_tokens"] > GPT5_1_TOKEN_DAILY_LIMIT:
        logger.error("Token usage exceeded daily limit for model %s", GPT5_1_NAME)
        return None, "ERROR: Token usage exceeded daily limit"
    
    llm_judge = GPT5_1.with_structured_output(EvaluationResult)
    prompt = EVALUATION_PROMPT.format(
        ground_truth=fault_scenario.get("RCA_gt", ""),
        rca_analysis=rca_analysis
    )
    try:

        config = {
            "run_name" : "LLM as a Judge",
            "tags": ["evaluation"]
        }

        if langsmith_metadata:
            config["metadata"] = langsmith_metadata

        result = llm_judge.invoke(prompt, config) # type: ignore
        score = getattr(result, "score", None)
        explanation = getattr(result, "reasoning", "")
        return score, explanation
    except Exception as e:
        logger.error("LLM evaluation failed: %s", str(e))
        return None, f"ERROR: LLM evaluation failed: {str(e)}"
    
def evaluate_experiment(fault_scenario: dict, report: dict)-> dict:
    agent_conf_name = report.get("agent_configuration_name", "N/A")
    formatted_scenario = f"{fault_scenario.get('scenario')} - {fault_scenario.get('fault_type')}"
    logger.info(
        "Evaluating experiment for agent configuration: %s, scenario: %s",
        agent_conf_name,
        formatted_scenario
    )

    llmJudge_metadata = {
        "agent_configuration_name" : report.get("agent_configuration_name"),
        "agent_id" : report.get("agent_id"),
        "scenario" : fault_scenario.get("scenario"),
        "fault_type" : fault_scenario.get("fault_type")
    }

    evaluation = {}

    detection = report.get("final_report", {}).get("detection", False)

    localization = report.get("final_report", {}).get("localization", [])
    if isinstance(localization, list):
        localization_str = ", ".join(localization)
    else:
        localization_str = ""

    rca_analtysis = report.get("final_report", {}).get("root_cause", "")

    evaluation["detection"] = evaluate_detection(fault_scenario, detection)
    evaluation["localization"] = evaluate_localization(fault_scenario, localization_str)
    evaluation["localization_any"] = evaluate_localization(fault_scenario, localization_str, any_of=True)
    target = fault_scenario.get("target")
    evaluation["localization_legacy"] = target in localization_str if target else not localization_str  # old substring rule
    evaluation["blamed_harness"] = blamed_harness(localization_str)
    evaluation["rca_score"], evaluation["rca_motivation"] = evaluate_rca_analysis(fault_scenario, rca_analtysis, llmJudge_metadata)

    return evaluation
