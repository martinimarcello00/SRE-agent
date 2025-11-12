import json
from pathlib import Path
import logging


logger = logging.getLogger(__name__)


def load_fault_scenarios(scenarios_dir=None):
    """
    Load all JSON files from the fault-scenarios directory.
    
    Args:
        scenarios_dir: Path to the fault-scenarios directory. 
                      If None, uses the fault-scenarios folder relative to this file.
    
    Returns:
        list: List of dictionaries containing the fault scenarios
    """
    if scenarios_dir is None:
        # Use the fault-scenarios subdirectory
        scenarios_dir = Path(__file__).parent / "fault-scenarios"
    else:
        scenarios_dir = Path(scenarios_dir)
    
    logger.info("Loading fault scenarios from: %s", scenarios_dir.absolute())
    
    # Verify the directory exists
    if not scenarios_dir.exists():
        logger.error("Directory not found: %s", scenarios_dir)
        return []
    
    scenarios = []
    
    # Get all JSON files in the directory
    json_files = sorted(scenarios_dir.glob("*.json"))
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                scenario = json.load(f)
                # Add the filename as metadata
                scenario['_source_file'] = json_file.name
                scenarios.append(scenario)
                logger.info("Loaded scenario: %s", json_file.name)
        except json.JSONDecodeError as e:
            logger.error("Error loading %s: %s", json_file.name, e)
        except Exception as e:
            logger.exception("Unexpected error with %s: %s", json_file.name, e)
    
    logger.info("Total scenarios loaded: %s", len(scenarios))
    return scenarios