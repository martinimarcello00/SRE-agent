# 🚀 Agent-Based SRE: Automated Diagnosis and Mitigation in K8s

## 📁 Repository Structure

```
SRE-agent/
│
├── � MCP-server/                            # Model Context Protocol Server
│   ├── mcp_server.py                         # Main MCP server implementation
│   ├── base_k8s_client.py                    # Kubernetes client wrapper
│   ├── prometheus_api.py                     # Prometheus metrics API
│   ├── jaeger_api.py                         # Jaeger tracing integration
│   ├── log_api.py                            # Log aggregation API
│   ├── datagraph.py                          # Service dependency graph
│   ├── config_manager.py                     # Configuration management
│   ├── pyproject.toml                        # MCP server dependencies
│   ├── README.md                             # MCP server documentation
│   └── service-graph/                        # Service topology definitions
│       ├── hotel-reservation-datagraph.txt
│       └── README.md
│
├── � archive/                               # Previous Work (Multidisciplinary Project)
│   ├── README.md                             # Archive documentation with full details
│   └── multidisciplinary-project/            # Archived project work
│       ├── report.pdf                        # Full project report
│       ├── slide-deck.pdf                    # Project presentation
│       ├── notebooks/                        # Jupyter notebooks (ReAct agents)
│       ├── studio/                           # LangGraph implementations
│       ├── results/                          # Experiment outputs
│       └── plots/                            # Visualizations
│
├── 📋 pyproject.toml                         # Root Python dependencies
├── 📋 poetry.lock                            # Locked dependencies
└── 📄 README.md                              # This file
```

## � Documentation

- **[MCP Server](MCP-server/README.md)**: Model Context Protocol server for Kubernetes observability
- **[Archive](archive/README.md)**: Previous multidisciplinary project work (complete documentation, diagrams, and results)
