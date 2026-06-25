# Quality Checking

Quality Checker Skills provide semantic critique and repair guidance. Deterministic Guards remain the hard gate for syntax, restricted execution, leakage, and numerical stability.

## Review Priorities

1. No future information leakage.
2. Correct and internally consistent calculation.
3. Economically interpretable factor logic.
4. Numerical stability around division, logarithms, overflow, and missing values.
5. Efficient vectorized implementation.
6. One clear idea without decorative stacking.

## Verdicts

- `accept`: the candidate is semantically sound enough for deterministic guard execution and fitness evaluation.
- `repair`: the candidate has fixable issues; include precise repair guidance or a repaired candidate when the skill's role permits it.
- `reject`: the candidate is not worth repairing because the core logic is invalid, unsafe, or incoherent.
