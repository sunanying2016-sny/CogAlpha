# Trace-Grounded Learning

Skill utility and prompt evolution must be grounded in recorded CogAlpha
execution traces. Utility can guide selection; it cannot automatically promote a
prompt change.

## Utility Update

- Increment usage when a trace records a skill invocation.
- Increase utility when downstream candidate stages become `qualified` or
  `elite`.
- Decrease or leave utility bounded when candidates are rejected by quality or
  fitness gates.
- Store the trace `evidence_id` used for the update.
- Keep utility bounded so one lucky or failed run cannot dominate the library.

## Evidence IDs

Every utility update and skill-change proposal must cite an `evidence_id` that
points to a trace event, replay report, or governance record. Evidence should
show the skill invocation, candidate lifecycle transition, and fitness outcome.

## Review Gates

Prompt or workflow changes require a human or reviewer gate before promotion.
A promoted change must include:

- `evidence_id`: the trace-grounded reason for the change.
- `reviewer`: the reviewer or role that approved the change.
- `rollback`: the previous file, version, or commit that can restore behavior.

## Rollback Requirements

Every promoted skill change must be reversible. Record `rollback` before status
is set to `promote`; otherwise the proposal must remain draft, hold, or reject.

## Non-Promotion Rule

Utility records may rank or select skills for execution, but they do not edit
SKILL.md files, alter default prompts, or promote workspace overlays by
themselves.
