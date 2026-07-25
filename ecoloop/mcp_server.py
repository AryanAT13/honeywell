"""MCP surface over the capability layer.

Registration only. Everything these tools do lives in tools.py, which the in-process agent
calls directly, so the protocol surface cannot drift from what the agent actually uses.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools

INSTRUCTIONS = """
Eco-Loop exposes an EnergyPlus building model and its supervisory control loop.

Start with inspect_model to see what can be actuated, and list_runs to see what has already
been simulated. check_policy projects a proposed policy onto the safe envelope without
running anything; evaluate_policy runs it and scores it against the untouched baseline.

Occupied setpoints are clamped into a fixed comfort band, so widening the band is not a way
to save energy. Supply air temperature is the lever with real headroom in this model.
"""

server = FastMCP("ecoloop", instructions=INSTRUCTIONS.strip())

for capability in (
    tools.list_models,
    tools.list_climates,
    tools.inspect_model,
    tools.list_runs,
    tools.run_kpis,
    tools.run_errors,
    tools.telemetry,
    tools.state_digest,
    tools.check_policy,
    tools.evaluate_policy,
    tools.compare_arms,
):
    server.tool()(capability)


def main() -> None:
    import sys

    transport = "streamable-http" if "--http" in sys.argv else "stdio"
    server.run(transport=transport)


if __name__ == "__main__":
    main()
