# Paper Protocol Artifacts

CogAlpha skills emit the paper-defined text artifacts described in `SKILL.md`.
The runtime parses those artifacts into internal Python models for DAG state,
tracing, guard execution, and metrics. Skills themselves must not ask the model
to emit structured data objects.

## Domain And Evolution Factors

Domain agent, crossover, mutation, repair, and logic-improvement skills emit one
or more Python factor functions wrapped exactly as:

```text
<<function N>>
def factor_xyz(df):
    """Explain the logic and formula."""
    df_copy = df.copy()
    return df_copy["factor_xyz"]
<</function N>>
```

The harness extracts each factor function artifact and derives the internal candidate
record from the function name, code, docstring, required OHLCV columns, request
generation, parent ids, and invoked skill name.

## Code Quality

The code-quality skill starts with exactly one of:

```text
The code is correct.
```

or:

```text
The code needs some adjustments.
```

When adjustments are needed, the response includes issue guidance followed by a
corrected `<<function N>>` block.

## Judge

The judge skill emits the paper-defined review fields:

```text
Practical Soundness: ...

Final Recommendation: Accept / Reject

Feedback for Improvement: ...
```

The harness maps `Accept` to an internal accept decision and `Reject` to a
repair/improvement path.

## Internal Models

After parsing, the runtime stores internal `AlphaCandidate`,
`AlphaCandidateBatch`, and `QualityDecision` objects. Those model names are
implementation details for the harness and trace artifacts, not model-facing
skill output formats.
