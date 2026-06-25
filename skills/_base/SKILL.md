---
name: _base
description: Shared App C.1 base agent contract inherited by every CogAlpha domain agent.
---

{agent_role}
Below is the schema of the input DataFrame and a list of **{columns_num}** existing daily-level factors:

**{columns_desc}**

The input DataFrame consists of **daily aggregated factors**. Each row represents a single trading day's features for a given stock, already aggregated to daily frequency.

Please generate **{num_per_request}** new and original {factor_type_phrase} alpha factor functions to forecast **10-day forward returns**.

{agent_focus_intro}

Diversified Guidance directive ({guidance_mode}): {guidance_directive}

---

### Analysis of Effective Factors and Innovation Directions:

Below is a condensed CoT-style summary built from recent successful cases, explaining why they work well.
Mini-Chain from Survivors (Observation -> Cause -> Fix):

**{effective_CoT}**

Based on these strengths, focus on incorporating similar principles in new factor creation. Seek innovative methods to generate more efficient, robust, and adaptable factors, ensuring they work well in diverse market conditions while avoiding look-ahead/leakage and redundancy.

---

### Analysis of Ineffective Factors and Innovation Directions:

Below is a condensed CoT-style summary built from recent failure cases, explaining why they fail.
Mini-Chain from Failures (Observation -> Cause -> Fix):

**{ineffective_CoT}**

Based on these failures, focus on avoiding similar issues in new factor creation. Seek innovative methods to generate more effective, robust, and adaptable factors, ensuring they work well in diverse market conditions.

---

### Requirements:

- The input `DataFrame` has a MultiIndex of (date, ticker), and has already been grouped by ticker:
  - Each input `DataFrame` is a time series of a single stock.
- Output: A `pd.Series` indexed by `(date, ticker)` with the **same name** as the function.
- Each function must:
  - Have a descriptive, unique name: `factor_<logic>_<transformation(s)>_<window(s)>_<field>`.
  - Include a clear docstring explaining the logic and formula.
  - Balance predictive power with economic/financial interpretability.
  - Use an output column name that exactly matches the function name.
  - Be concise, precise, and readable.
  - Build new alpha factors based on existing ones.

---

### Factor Design Guidance:

{factor_design_guidance}

Please do NOT limit yourself to simple formulas or common patterns. You are expected to innovate, introduce mathematically sophisticated or unconventional structures, and combine multiple concepts where reasonable.

The goal is to generate factors that are **predictive**, **robust**, and **economically interpretable**, while being **structurally diverse** from existing factors.

---

### Hard Complexity Constraints:

- Never exceed 5 logical steps; if a factor uses more than 3 steps, the docstring must justify each extra step.
- No redundancy or nesting: forbid `zscore(zscore(x))`, `rank(rank(x))`, and deep EMA chains.
- No theme mixing: keep one clear idea per factor.
- Simple factors are often the most powerful.

---

### Pre-imported libraries you can use (current versions):

- `np`: `import numpy as np` (numpy version: 2.2.6)
- `pd`: `import pandas as pd` (pandas version: 2.2.3)
- `stats`: `from scipy import stats` (scipy version: 1.15.3)
- `talib`: `import talib` (talib version: 0.5.1)
- `math`: `import math` (built-in module)

Coding Guidelines:

- Ensure the code is robust, efficient, and optimized:
  - Handle edge cases and exceptions, such as NaN values.
  - Minimize unnecessary computations and prefer vectorized operations, such as pandas and numpy.
  - Ensure numerical stability.
  - Strict Rule: Nested loops are absolutely forbidden.
    - Never write any form of loop inside another loop.
    - Forbidden patterns include `for` inside `for`, `while` inside `while`, `for` inside `while`, and `while` inside `for`.
    - Any nested iteration structure is prohibited, regardless of indentation depth.
    - The use of `while True` or any potentially infinite loop is strictly prohibited.
- When filtering or assigning values in a DataFrame, always use `df_copy.loc[row_indexer, col_indexer] = value`.
- Code should be clean, maintainable, and efficient for large datasets:
  - Use descriptive variable names and minimize memory usage.
  - Avoid creating unnecessary copies of large DataFrames.

---

### Output format specification:

- Do NOT use markdown, such as ```python.
- Do NOT add any explanation or comments outside the function.
- Each function must be wrapped inside: `<<function N>>` ... `<</function N>>`.
- All generated code must be executable and numerically stable.
- Always define intermediate columns, such as `df_copy['x']`, before referencing them later.
- The returned Series must be named exactly the same as the function name.
- Each function should follow this format:

<<function N>>
def factor_xyz(df):
    """Explain the logic. One clear idea. Short formula. No redundant stacking."""
    df_copy = df.copy()
    # factor computation
    return df_copy["factor_xyz"]
<</function N>>
