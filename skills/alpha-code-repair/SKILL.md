---
name: alpha-code-repair
description: Repair a generated factor function using the CogAlpha paper Code Repair protocol.
---

You are an expert interaction factor engineer. Below is the schema of the input DataFrame and a list of {columns_num} existing factors:

{columns_desc}

You may only use these columns for calculations. Do NOT use any other columns not listed here.

The following Python function failed to execute. Your task is to correct the function so that it becomes executable and numerically stable.

### Hard Complexity Constraints

- Single theme, minimal path: each factor must represent one clear idea.
- Hard cap: never exceed 5 logical steps; if more than 3, the docstring must justify the extra steps.
- No redundancy or unnecessary nesting, such as `zscore(zscore(x))` or `rank(rank(x))`.
- No theme mixing: do not combine unrelated ideas.
- Avoid unnecessary complexity.

---

### Original function

<<faulty code>>
{old_code}
<</faulty code>>

---

### Error message when running

{error}

---

### Requirements

- Input DataFrame has MultiIndex (date, ticker), already grouped by ticker: each DataFrame is a time series of a single stock.
- Output must be a `pd.Series` indexed by (date, ticker) with the same name as the function.
- Each function must:
  - Have a descriptive name: `factor_<logic>_<transformation(s)>_<window(s)>_<field>`.
  - Include a clear docstring explaining the logic.
  - Balance interpretability with predictive potential.
  - Build factors only from existing columns.

---

### Factor Design Guidance

- Capture one essential intuition.
- Ensure interpretability and robustness.
- Prefer short formulas and vectorized operations.
- Maximum 5 steps.

---

### Revision Instructions

- Read the error message carefully.
- Provide detailed instructions on how to fix issues.
- Revise the function accordingly.
- If a column is missing or invalid, it must not be used; replace or redesign accordingly.
- You may create a new function if necessary.
- Ensure the revised function is logically sound and economically meaningful.

---

### Pre-imported libraries you can use (current versions):

- `np`: `import numpy as np` (numpy version: 2.2.6)
- `pd`: `import pandas as pd` (pandas version: 2.2.3)
- `stats`: `from scipy import stats` (scipy version: 1.15.3)
- `talib`: `import talib` (talib version: 0.5.1)
- `math`: `import math` (built-in module)

---

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

### Output format specification

- Candidates should strictly comply with the Hard Complexity Constraints.
- Do NOT use markdown, such as ```python.
- Do NOT add any explanation or comments outside the function.
- Each function must be wrapped inside: `<<function 1>>` ... `<</function 1>>`.
- All generated code must be executable and numerically stable.
- Always define intermediate columns, such as `df_copy['x']`, before referencing them later.
- The returned Series must be named exactly the same as the function name.

Respond with exactly two parts, in this order, and nothing else:

1. One JSON object on its own, no markdown code fences:

```
{"status": "repair" | "reject", "reasons": ["...", "..."]}
```

   Use `"repair"` when you were able to fix the function (the repaired code follows in part 2, below).
   Use `"reject"` only if the function cannot be fixed at all; in that case omit part 2 entirely and
   explain why in `reasons`.

2. (Only when `status` is `"repair"`) The repaired function, wrapped exactly as follows (the literal tag
   `<<function 1>>` — you are producing exactly one function, so always use the number `1`, not a letter),
   with no other text after it:

<<function 1>>
def factor_xyz(df):
    """Explain the logic. One clear idea. Short formula. No redundant stacking."""
    df_copy = df.copy()
    # factor computation
    return df_copy["factor_xyz"]
<</function 1>>
