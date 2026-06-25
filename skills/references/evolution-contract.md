# Evolution Contract

Thinking Evolution skills transform qualified AlphaCandidates while preserving traceability.

## Mutation

Mutation takes one parent candidate and returns a new AlphaCandidate that:

- preserves the parent's core economic intuition
- changes the implementation meaningfully
- improves robustness, stability, or predictive potential
- records the parent id and `operation: "mutation"`

## Crossover

Crossover takes two parent candidates and returns a new AlphaCandidate that:

- combines complementary insights from both parents
- avoids simple addition or averaging unless economically justified
- introduces a coherent interaction, gate, normalization, or regime dependency
- records both parent ids and `operation: "crossover"`

## Rules

- The child candidate must still obey the Alpha Factor Contract.
- Do not copy parent code with superficial variable renaming.
- Keep the child's logic interpretable and compact.
