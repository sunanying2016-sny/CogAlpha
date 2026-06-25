# CogAlpha Skills

This directory contains the Standard Skills used by the CogAlpha Skill-Driven DAG.

Skill categories:

- `alpha-*` domain skills map one-to-one to the 21 paper-defined Seven-Level Agent Hierarchy agents.
- `alpha-code-*`, `alpha-judge`, and `alpha-logic-improvement` implement the paper-defined Multi-Agent Quality Checker roles.
- `alpha-mutation` and `alpha-crossover` implement the paper-defined Thinking Evolution operators.

Shared contracts live in `references/` and are intentionally one level deep so a skill can load only the extra context it needs.
