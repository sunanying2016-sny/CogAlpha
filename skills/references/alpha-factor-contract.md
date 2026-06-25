# Alpha Factor Contract

All generated factors must obey this contract.

## Input

- The input is a pandas DataFrame for one stock's daily time series.
- Rows are sorted from earliest date to latest date.
- The index is preserved and represents `(date, ticker)` in the wider dataset.
- The only input columns are `open`, `high`, `low`, `close`, and `volume`.

## Output

- Return one pandas Series with the same index as the input DataFrame.
- The Series name must exactly match the Python function name.
- The function name must start with `factor_` and be descriptive.

## Alpha Runtime Allowlist

Generated code may use only these pre-imported names:

- `np` for numpy
- `pd` for pandas
- `stats` for scipy.stats
- `talib` for TA-Lib
- `math` for Python math

## Hard Rules

- Use only present and past observations.
- Never use negative shifts, centered rolling windows, reversed time order, future labels, or target returns.
- Do not import libraries outside the allowlist.
- Do not use `eval`, `exec`, file IO, network IO, subprocesses, or global mutable state.
- Avoid nested loops and infinite loops.
- Prefer vectorized pandas/numpy operations.
- Keep one clear economic idea per factor.
- Keep the factor concise; if more than 3 logical steps are needed, the rationale must explain why.

## Function Shape

```python
def factor_example_name(df):
    """One clear rationale and short formula."""
    df_copy = df.copy()
    # factor computation
    df_copy["factor_example_name"] = ...
    return df_copy["factor_example_name"]
```
