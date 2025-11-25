from models import EvaluationResult
from prompts import EVALUATION_PROMPT
from config import GPT5_1
from utils import get_today_model_usage
import logging
from typing import Optional

GPT5_1_NAME = "gpt-5-2025-08-07"
GPT5_1_TOKEN_DAILY_LIMIT = 240_000

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

def evaluate_localization(fault_scenario: dict, localization: str) -> bool:
    """
    Evaluates whether the localization result matches the ground truth target in the fault scenario.

    Args:
        fault_scenario (dict): The fault scenario dictionary, expected to contain a "target" key.
        localization (str): The localization result to evaluate.

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

    # Check if the target is contained in the localization string
    return target in localization

def evaluate_rca_analysis(fault_scenario: dict, rca_analysis: str, langsmith_metadata: Optional[dict] = None) -> tuple[Optional[int], str]:
    """
    Evaluates the root cause analysis (RCA) result using an LLM and returns a score and explanation.

    Args:
        fault_scenario (dict): The fault scenario dictionary, expected to contain an "RCA_gt" key.
        rca_analysis (str): The RCA analysis result to evaluate.

    Returns:
        tuple[Optional[int], str]: A tuple containing the evaluation score (or None on error) and an explanation string.
    """
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
    evaluation["rca_score"], evaluation["rca_motivation"] = evaluate_rca_analysis(fault_scenario, rca_analtysis, llmJudge_metadata)

    return evaluation
