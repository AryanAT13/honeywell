"""The MCP surface, exercised through a real client session."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connected

from ecoloop import tools
from ecoloop.mcp_server import server

REGISTERED = (
    "list_models",
    "list_climates",
    "inspect_model",
    "commission_model",
    "list_runs",
    "run_kpis",
    "run_errors",
    "run_decisions",
    "telemetry",
    "state_digest",
    "check_policy",
    "evaluate_policy",
    "compare_arms",
)


def call(name: str, arguments: dict):
    async def go():
        async with connected(server) as client:
            return await client.call_tool(name, arguments)

    result = asyncio.run(go())
    if result.isError:
        return result, None
    return result, result.structuredContent or json.loads(result.content[0].text)


def test_every_capability_is_registered_with_a_description():
    async def go():
        async with connected(server) as client:
            return await client.list_tools()

    listed = asyncio.run(go())
    by_name = {tool.name: tool for tool in listed.tools}
    assert set(by_name) == set(REGISTERED)
    assert all(tool.description for tool in listed.tools)


def test_schemas_are_published_for_structured_arguments():
    async def go():
        async with connected(server) as client:
            return await client.list_tools()

    listed = asyncio.run(go())
    evaluate = next(t for t in listed.tools if t.name == "evaluate_policy")
    assert "policy" in evaluate.inputSchema["properties"]
    assert evaluate.inputSchema["required"] == ["policy"]


def test_a_policy_round_trips_through_the_protocol():
    _, payload = call(
        "check_policy",
        {"policy": {"occupied": {"heating": 18.0, "cooling": 29.0}, "reason": "save energy"}},
    )
    assert payload["accepted"]["occupied"] == {"heating": 21.0, "cooling": 24.0}
    assert payload["clamped"]["occupied.cooling"] == 1


def test_a_bad_argument_is_reported_rather_than_crashing_the_server():
    result, _ = call("inspect_model", {"model": "no-such-building"})
    assert result.isError
    assert "available" in result.content[0].text

    _, payload = call("list_climates", {})
    assert payload["result"] == sorted(tools.list_climates())


@pytest.mark.slow
def test_the_loop_can_be_closed_from_outside_this_codebase():
    """An external client proposes a policy, the simulation runs, the score comes back."""
    _, payload = call(
        "evaluate_policy",
        {"policy": {"supply_air_temperature": 18.0}, "period": "01-15:01-16"},
    )
    assert payload["electricity_pct"] < -5.0
    assert payload["review"]["clamped"] == {}
