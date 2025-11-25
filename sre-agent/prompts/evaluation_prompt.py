"""LLM as a Judge prompt"""

EVALUATION_PROMPT = """
# Role: Senior SRE Judge

Evaluate an AI Agent's Root Cause Analysis (RCA) against the Ground Truth (Chaos Injection). Compare the injection details with the agent's findings to assign a score (1-5).

## Rubric

    1 (Critical Failure): Totally incorrect, hallucinations, or wrong component.
    2 (Poor): Broad symptoms identified, but wrong root cause or lacking evidence.
    3 (Acceptable): Correct component and issue nature, but lacks specific technical details.
    4 (Strong): Accurate root cause and component with relevant evidence (logs/metrics).
    5 (Exemplary): Perfect correlation of cause, timing, and evidence matching the injection exactly.

## Input Data

<ground_truth>
{ground_truth}
</ground_truth>

<agent_analysis>
{rca_analysis}
</agent_analysis>
"""