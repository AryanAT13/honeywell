# 1. Control through the EnergyPlus runtime API

Status: accepted

## Context

The closed loop can be built two ways. The common approach is to run a simulation, read its
output files, edit the IDF, and run again. The alternative is the EnergyPlus Python API,
which exposes callbacks during the run where variables can be read and actuators written on
the live instance.

The brief requires that computed setpoints "feed directly back into the active EnergyPlus
instance", and scores robustness over an extended simulation horizon at 30%.

## Decision

Supervisory control is applied through runtime API callbacks against a running instance.
Reads happen at `end_zone_timestep_after_zone_reporting`, actuator writes will happen at
`begin_zone_timestep_after_init_heat_balance`.

Model editing is retained for a separate, slower purpose: structural measures that cannot be
expressed as a setpoint, which produce new model versions and require a fresh run.

## Consequences

The loop is genuinely closed and runs at simulation speed; an annual run with full telemetry
takes 13.5 s. Two control paths must be maintained rather than one. The API's ordering
constraints are sharp and silent when violated, so they are handled in one place
(`runner.py`) and covered by a test that reconciles our energy totals against EnergyPlus's
own tabular report.
