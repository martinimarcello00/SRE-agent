import os
import json
from pathlib import Path


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
    
    print(f"Loading fault scenarios from: {scenarios_dir.absolute()}")
    
    # Verify the directory exists
    if not scenarios_dir.exists():
        print(f"✗ Error: Directory not found: {scenarios_dir}")
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
                print(f"✓ Loaded: {json_file.name}")
        except json.JSONDecodeError as e:
            print(f"✗ Error loading {json_file.name}: {e}")
        except Exception as e:
            print(f"✗ Unexpected error with {json_file.name}: {e}")
    
    print(f"\nTotal scenarios loaded: {len(scenarios)}")
    return scenarios