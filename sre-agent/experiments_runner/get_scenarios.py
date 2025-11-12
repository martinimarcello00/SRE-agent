import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def load_fault_scenarios(scenarios_dir=None, only_executable=True):
    """
    Load fault scenarios from JSON files in a specified directory.

    Args:
        scenarios_dir (str or Path, optional): Path to the directory containing fault scenario 
            JSON files. If None, uses the "fault-scenarios" folder relative to this file.
        only_executable (bool, optional): If True, only scenarios with "execute" set to True 
            are included. If False, all scenarios are loaded regardless of their "execute" flag.

    Returns:
        list: List of dictionaries, each representing a fault scenario loaded from a JSON file.
              Returns an empty list if the directory does not exist or no valid scenarios are found.
    """
    if scenarios_dir is None:
        scenarios_dir = Path(__file__).parent / "fault-scenarios"
    else:
        scenarios_dir = Path(scenarios_dir)
    
    logger.info("Loading fault scenarios from: %s", scenarios_dir.absolute())
    
    if not scenarios_dir.exists():
        logger.error("Directory not found: %s", scenarios_dir)
        return []
    
    scenarios = []
    json_files = sorted(scenarios_dir.glob("*.json"))
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                scenario = json.load(f)
                scenario['_source_file'] = json_file.name
                if scenario.get("execute", False) and only_executable:
                    scenarios.append(scenario)
                else:
                    logger.warning(
                        "Scenario %s skipped as user specified",
                        scenario.get("scenario", "unknown") + " - " + scenario.get("fault_type", "unknown")
                    )
                logger.info("Loaded scenario: %s", json_file.name)
        except json.JSONDecodeError as e:
            logger.error("Error loading %s: %s", json_file.name, e)
        except Exception as e:
            logger.exception("Unexpected error with %s: %s", json_file.name, e)
    
    logger.info("Total scenarios loaded: %s", len(scenarios))
    return scenarios