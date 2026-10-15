"""
14_agentic_workflow_and_tools.py - Asynchronous AI Agent Tool Execution Gateway
================================================================================

AI Agents (such as ReAct loops, planning agents, or multi-agent orchestrators)
spend most of their time calling external tools, searching vector databases,
executing Python code, and querying APIs.

When building an **Agent Gateway** or **MCP Tool Server** with Buckthorn:
1. **Microsecond Non-Blocking Suspension:** Buckthorn can handle thousands of
   concurrent pending tool calls simultaneously without blocking worker threads.
2. **Parallel Tool Dispatch:** Independent tools can be executed concurrently
   using Python's `asyncio.gather(...)`.
3. **Strict C Schema Validation:** Incoming tool requests and output plans are
   validated via C `yyjson` schemas at maximum speed.

This example demonstrates:
- Building an AI Agent Gateway API.
- Defining tools as async functions (`calculator`, `database_lookup`, `web_search`).
- Dispatching multiple tools concurrently via `asyncio.gather`.
- Returning a structured output schema.

To run this live server in development mode:
    buckthorn dev docs/examples/14_agentic_workflow_and_tools.py:app --reload

To run this script directly to verify all endpoints in-memory:
    python docs/examples/14_agentic_workflow_and_tools.py
"""

import asyncio
from typing import Optional
from buckthorn import Buckthorn, Schema, Field, status

app = Buckthorn(
    title="AI Agent Tool Dispatch Gateway",
    version="1.0.0",
)


# ==============================================================================
# Domain Schemas
# ==============================================================================
class AgentTaskRequest(Schema):
    query: str = Field(min_length=3, max_length=100)
    user_id: int = Field(ge=1)
    allow_web_search: bool = True


class AgentTaskResponse(Schema):
    task_id: str
    status: str
    tools_called: int
    computation_result: float
    knowledge_summary: str


# ==============================================================================
# Asynchronous Agent Tools
# ==============================================================================
async def tool_calculator(expression_val: float) -> float:
    """Simulates an external math execution tool."""
    await asyncio.sleep(0.002)
    return round(expression_val * 1.15, 2)  # e.g., apply 15% tax


async def tool_knowledge_lookup(query: str) -> str:
    """Simulates vector database embedding retrieval."""
    await asyncio.sleep(0.003)
    return f"Knowledge retrieved for '{query}': High-performance C11 engine."


async def tool_audit_recorder(user_id: int) -> bool:
    """Simulates recording agent execution in telemetry database."""
    await asyncio.sleep(0.001)
    return True


# ==============================================================================
# Agent Execution Endpoint
# ==============================================================================
@app.post("/agent/execute", response_model=AgentTaskResponse, status_code=status.HTTP_200_OK)
async def execute_agent_plan(task: AgentTaskRequest):
    """
    Receives an agent prompt, concurrently dispatches multiple tools
    via asyncio.gather, and formats the consolidated result.
    """
    # Parallel dispatch of tools: Calculator, Knowledge base, and Audit
    calc_res, kb_res, audit_ok = await asyncio.gather(
        tool_calculator(100.0),
        tool_knowledge_lookup(task.query),
        tool_audit_recorder(task.user_id),
    )

    return AgentTaskResponse(
        task_id="task_agt_9812",
        status="completed" if audit_ok else "failed",
        tools_called=3,
        computation_result=calc_res,
        knowledge_summary=kb_res,
    )


# ==============================================================================
# Self-Verification Test Runner
# ==============================================================================
if __name__ == "__main__":
    from buckthorn.testing import RawTestClient

    print("🚀 Running 14_agentic_workflow_and_tools.py in-memory verification...")
    client = RawTestClient(app)

    task_payload = {
        "query": "Optimize latency for AI agent tools",
        "user_id": 42,
        "allow_web_search": True,
    }

    res = client.post("/agent/execute", json=task_payload)
    assert res.status_code == 200
    plan = res.json()
    assert plan["task_id"] == "task_agt_9812"
    assert plan["status"] == "completed"
    assert plan["tools_called"] == 3
    assert round(plan["computation_result"], 2) == 115.0
    assert "High-performance C11 engine" in plan["knowledge_summary"]

    print(f"  ✓ Agent Plan Executed Concurrently -> 200")
    print(f"    Task ID: {plan['task_id']}")
    print(f"    Tools Called: {plan['tools_called']}")
    print(f"    Computation: {plan['computation_result']}")
    print(f"    Knowledge: {plan['knowledge_summary']}")

    print("\n✅ All 14_agentic_workflow_and_tools.py endpoints verified successfully!")
    print("👉 To run live: buckthorn dev docs/examples/14_agentic_workflow_and_tools.py:app")
