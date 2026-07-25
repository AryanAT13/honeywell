# 6. One capability layer, exposed both in process and over MCP

Status: accepted

## Context

The brief asks for an MCP server or custom agentic tools, and the agent must use them to
inspect files, extract runtime errors and act without human code changes.

There is a temptation to route the agent's own tool calls through MCP, on the grounds that
this is what makes the MCP integration real. It would be a mistake here. The policy author
runs inside the EnergyPlus timestep callback, in the same process as the simulation. Sending
each decision out over stdio and back adds serialisation and IPC latency to the one path that
is already the run's bottleneck, and buys nothing: there is no trust boundary to cross and no
second machine to reach.

Equally, an MCP server that only wraps a subset of what the agent can do is a demo prop
rather than an interface.

## Decision

Capabilities are defined once in `ecoloop/tools.py` as plain functions taking and returning
pydantic models, with no MCP import anywhere in the module. `ecoloop/mcp_server.py` is
registration and nothing else.

The in-process agent calls those functions directly. External clients — the MCP Inspector,
Claude Desktop, anything else that speaks the protocol — reach the same functions over stdio
or streamable HTTP.

## Consequences

One implementation, so the protocol surface cannot drift from what the agent actually uses,
and the agent pays no IPC cost per decision. Tool behaviour is testable without a transport,
and the transport is testable with an in-memory client session.

The server is genuinely useful rather than decorative: `evaluate_policy` runs a proposed
policy against the simulation and scores it, so an external model can close the loop through
the protocol without touching this codebase. That is also what makes the building
interrogable in natural language from a desktop client.
