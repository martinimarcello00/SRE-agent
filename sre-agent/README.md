# SRE Agent

## 📁 Project Structure

```
Studio/
├── sre-agent.py              # Main entry point
├── config/
│   ├── __init__.py
│   └── settings.py           # Environment & configuration
├── models/
│   ├── __init__.py
│   ├── schemas.py            # Pydantic models
│   └── states.py             # LangGraph state definitions
├── agents/
│   ├── __init__.py
│   ├── triage_agent.py       # Triage agent implementation
│   ├── planner_agent.py      # Planner agent implementation
│   ├── rca_agent.py          # RCA worker agent implementation
│   └── supervisor_agent.py   # Supervisor agent implementation
├── prompts/
│   ├── __init__.py
│   ├── triage_prompts.py     # Triage agent prompts
│   ├── planner_prompts.py    # Planner agent prompts
│   ├── rca_prompts.py        # RCA agent prompts
│   └── supervisor_prompts.py # Supervisor agent prompts
├── tools/
│   ├── __init__.py
│   ├── mcp_tools.py          # MCP client & tool setup
│   └── rca_tools.py          # RCA-specific tools
├── utils/
│   ├── __init__.py
│   └── helpers.py            # Utility functions
└── graph.py                  # Main workflow graph assembly
```

## 🚀 How to Use

### Using LangGraph Studio (Recommended)

LangGraph Studio provides a visual interface for developing and debugging the agent workflow:

```bash
# Navigate to the sre-agent directory
cd sre-agent

# Start LangGraph Studio
poetry run langgraph dev
```

This will:
- Launch the LangGraph Studio UI
- Enable visual debugging of the agent workflow
- Allow step-by-step execution and inspection
- Provide real-time state visualization

### Basic Usage
```python
# In sre-agent.py or any script
from graph import parent_graph

result = await parent_graph.ainvoke(initial_state)
```

### Modifying a Specific Component

#### Change a Prompt
```python
# Edit: prompts/rca_prompts.py
RCA_AGENT_PROMPT = """
Your modified prompt here...
"""
```

#### Modify Agent Logic
```python
# Edit: agents/rca_agent.py
async def rcaAgent(state: RcaAgentState):
    # Your modified logic here
    pass
```

#### Update Configuration
```python
# Edit: config/settings.py
MAX_TOOL_CALLS = 10  # Change budget
```

#### Add New Tools
```python
# Edit: tools/rca_tools.py
@tool
def my_new_tool(param: str) -> str:
    """My new tool description"""
    return "result"
```

## 📝 Module Documentation

### `config/settings.py`
- Environment variables loading
- LLM configuration (GPT5_MINI)
- Tool budget (MAX_TOOL_CALLS)
- MCP server configuration
- Allowed tools list

### `models/schemas.py`
- `Symptom`: Cluster symptom definition
- `SymptomList`: List of symptoms
- `RCATask`: Investigation task definition
- `RCATaskList`: List of RCA tasks
- `UpdateAgentData`: Agent step tracking
- `FinalReport`: Final diagnosis report

### `models/states.py`
- `TriageAgentState`: Triage workflow state
- `PlannerAgentState`: Planning workflow state
- `RcaAgentState`: RCA investigation state
- `SupervisorAgentState`: Supervision state
- `SreParentState`: Complete workflow state

### `agents/`
Each agent module contains:
- Data gathering functions
- Agent logic functions
- Graph building function
- Exported compiled graph

### `prompts/`
Prompt templates for each agent:
- System prompts (constants)
- ChatPromptTemplate instances
- Prompt formatting logic

### `tools/`
- `mcp_tools.py`: MCP client setup and tool filtering
- `rca_tools.py`: Custom RCA tools (submit_final_diagnosis)

### `utils/helpers.py`
Helper functions:
- `get_insights_str()`: Format insights
- `get_prev_steps_str()`: Format previous steps
- `count_tool_calls()`: Count tool usage
- `count_non_submission_tool_calls()`: Count investigation tools

## 🔧 Common Tasks

### Adding a New Agent

1. Create `agents/my_agent.py`:
```python
from langgraph.graph import START, END, StateGraph
from models import MyAgentState

def my_agent(state: MyAgentState):
    # Your logic
    return {"result": "value"}

def build_my_agent_graph():
    builder = StateGraph(MyAgentState)
    builder.add_node("my-agent", my_agent)
    builder.add_edge(START, "my-agent")
    builder.add_edge("my-agent", END)
    return builder.compile()

my_agent_graph = build_my_agent_graph()
```

2. Add state to `models/states.py`
3. Add prompts to `prompts/my_prompts.py`
4. Integrate into `graph.py`

### Debugging a Specific Agent

```python
# Test triage agent in isolation
from agents.triage_agent import triage_agent_graph

test_state = {
    "app_name": "Test App",
    "app_summary": "Test summary",
    # ... other required fields
}

result = await triage_agent_graph.ainvoke(test_state)
print(result["symptoms"])
```

### Modifying Prompt Behavior

```python
# Edit prompts/rca_prompts.py
RCA_AGENT_PROMPT = """
Your new instructions here...
Use {app_summary}, {investigation_goal}, etc.
"""

# Changes are automatically picked up by agents/rca_agent.py
```

## 📦 Dependencies

- `langchain-openai`: LLM integration
- `langgraph`: Agent orchestration
- `pydantic`: Data validation
- `langchain-mcp-adapters`: MCP tool integration
- Custom API modules from `../MCP-server/api/`

## 🐛 Troubleshooting

### Import Errors
```bash
# Ensure MCP-server path is correct in config/settings.py
# Check that you're running from the Studio/ directory
cd /home/vm-kubernetes/SRE-agent/Studio
python sre-agent.py
```

### Agent Not Working
1. Check the agent file in `agents/`
2. Verify state definition in `models/states.py`
3. Check prompts in `prompts/`
4. Enable debug logging

### Tool Errors
1. Check tool configuration in `config/settings.py`
2. Verify MCP server is running
3. Check tool permissions in `TOOLS_ALLOWED`