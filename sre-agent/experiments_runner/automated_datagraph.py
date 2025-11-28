"""
Automated Datagraph Management for Experiments

This module handles updating the Neo4j service graph (datagraph) based on the scenario being tested.
It maps scenario names to their corresponding datagraph configuration files and manages the
drop/recreate process.
"""

import logging
import os
import sys
from pathlib import Path

# Add MCP-server to path for importing DataGraph
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "MCP-server")))
from api.datagraph import DataGraph

logger = logging.getLogger(__name__)

# Mapping of scenario names to their datagraph config files
SCENARIO_DATAGRAPH_MAP = {
    "hotel reservation": "hotel-reservation-datagraph.txt",
    "astronomy shop" : "astronomy-shop-datagraph.txt"
}

# Directory containing datagraph config files
DATAGRAPH_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "MCP-server" / "service-graph"


def update_datagraph_for_scenario(scenario_name: str):
    """
    Updates the Neo4j datagraph for a given scenario.
    
    This function:
    1. Maps the scenario name to its corresponding datagraph config file
    2. Connects to Neo4j
    3. Drops the existing datagraph
    4. Creates a new datagraph from the config file
    
    Args:
        scenario_name (str): Name of the scenario to update the datagraph for.
                           Should match keys in SCENARIO_DATAGRAPH_MAP.
    
    Raises:
        Exception: If datagraph update fails for any reason.
    """
    # Normalize scenario name to lowercase for matching
    scenario_key = scenario_name.lower()
    
    if scenario_key not in SCENARIO_DATAGRAPH_MAP:
        logger.warning(f"No datagraph mapping found for scenario '{scenario_name}'. Skipping datagraph update.")
        return
    
    config_file = SCENARIO_DATAGRAPH_MAP[scenario_key]
    config_path = DATAGRAPH_CONFIG_DIR / config_file
    
    if not config_path.exists():
        logger.error(f"Datagraph config file not found: {config_path}")
        return
    
    try:
        logger.info(f"Updating datagraph for scenario '{scenario_name}' using {config_file}")
        
        # Initialize DataGraph connection
        dg = DataGraph()
        
        # Drop existing datagraph
        logger.info("Dropping existing datagraph...")
        dg.drop_datagraph(confirmation=True)
        
        # Create new datagraph from config file
        logger.info(f"Creating datagraph from {config_path}")
        dg.create_datagraph(str(config_path))
        
        # Close connection
        dg.close()
        
        logger.info(f"Successfully updated datagraph for scenario '{scenario_name}'")
        
    except Exception as e:
        logger.error(f"Failed to update datagraph for scenario '{scenario_name}': {e}")
        raise
