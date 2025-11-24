# RCA analyses reducer
def merge_rca_analyses(left: list[dict], right: list[dict]) -> list[dict]:
    """Merge RCA analyses lists, deduplicating by task priority.
    
    Args:
        left: Existing analyses
        right: New analyses to merge
        
    Returns:
        Combined list with one analysis per priority
    """
    # Build map from priority to analysis
    priority_map = {}
    for analysis in left + right:
        if isinstance(analysis, dict):
            task = analysis.get("task", {})
            priority = task.get("priority") if isinstance(task, dict) else None
            if priority is not None:
                priority_map[priority] = analysis
    
    # Return sorted by priority
    return [priority_map[p] for p in sorted(priority_map.keys())]