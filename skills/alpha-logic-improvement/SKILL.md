---
name: alpha-logic-improvement
description: Improve a factor that failed the CogAlpha paper Judge Agent.
---

You are an expert interaction factor engineer. Below is the schema of the input DataFrame and a list of {columns_num} existing factors:

{columns_desc}

You may only use these columns for calculations. Do NOT use any other columns not listed here.

The following Python function was reviewed and did NOT pass the logical soundness evaluation. Your task is to revise and improve this function so that:

1. It is economically and financially interpretable.
2. It is logically sound according to financial principles.
3. It addresses the specific feedback provided below.

---

### Original function

<<previous function>>
{old_code}
<</previous function>>

---

### Hard Complexity Constraints

Remember: Simple factors are often the most powerful and stable.

- Single theme, minimal path: each factor must represent one clear idea.
- Hard cap: never exceed 5 logical steps in total; if more than 3 steps are used, the docstring must justify each extra step's necessity.
- No redundancy or decorative transforms such as `zscore(zscore(x))`, `rank(rank(x))`, or deep EMA chains without rationale.
- No theme mixing: do not combine unrelated ideas.
- Avoid nested or layered operations.
- Avoid unnecessary complexity or logic stacking.

### JudgeAgent feedback (reason for rejection)

{dynamic_feedback}

### Requirements

- The input `DataFrame` has a MultiIndex of (date, ticker), and has already been grouped by ticker:
  - Each input `DataFrame` is a time series of a single stock.
- Output: A `pd.Series` indexed by (date, ticker) with the same name as the function.
- Each function must:
  - Have a descriptive, unique name: `factor_<logic>_<transformation(s)>_<window(s)>_<field>`.
  - Include a clear docstring explaining the logic and formula.
  - Balance predictive power with economic/financial interpretability.
  - The output column name must match the function name.
  - Be concise, precise, and readable.
  - Build new alpha factors based on existing ones.

### Factor Design Guidance

- Focus on capturing the essential intuition of the assigned theme.
- Ensure the logic is interpretable, robust, and implementable in a few steps.
- Prefer clean, generalizable formulas over highly engineered constructs.
- Each factor should be expressible in a short formula or no more than 5 logical steps.
- Balance simplicity with predictive potential: avoid trivial duplication, but also avoid unnecessary complexity.

---

### Revision instructions

- Carefully read the JudgeAgent feedback.
- Provide detailed instructions on how to fix the issues raised.
- Revise the function accordingly to address the issues pointed out.
- You may create a new one if you believe the given function is too flawed to fix.
- Ensure the revised function is economically meaningful, logically sound, and well-structured.
- You may introduce new logic, transformations, or corrections as needed.
- Make sure the output is a `pandas.Series` indexed by (date, ticker).

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
- Before generating the code, provide detailed instructions on how to fix the issues raised.
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
