"""Static deterministic guards for generated alpha function code."""

from __future__ import annotations

import ast
import re

from cogalpha.alpha_contract import (
    ALLOWED_IMPORT_MODULES,
    FORBIDDEN_ALPHA_CALLS,
    FORBIDDEN_TIME_ORDER_PATTERNS,
    is_ohlcv_or_factor_column,
)
from cogalpha.schemas import GuardIssue, GuardReport, GuardStatus

MAX_STATIC_RANGE_SIZE = 100_000

# Pandas time-series methods whose negative `periods` (positional or keyword)
# reach into future rows -- a temporal leakage (D-04). `.shift` is the canonical
# case; `.diff` / `.pct_change` accept the same `periods=` semantics.
_NEGATIVE_PERIOD_METHODS = ("shift", "diff", "pct_change")


def run_temporal_leakage_static_scan(
    code: str, function_name: str | None = None
) -> list[GuardIssue]:
    """Return ONLY the temporal-leakage issues for a factor code string (D-04).

    This is the single source for the temporal subset of the static guard:
    forward-looking shifts (positional ``shift(-k)`` and the keyword
    ``shift(periods=-k)`` form), negative-period ``diff`` / ``pct_change``,
    centered/forward rolling windows, absolute-future indexing, and reverse
    time-order patterns (``iloc[::-1]`` / ``sort_index(ascending=False)``).
    ``run_static_alpha_code_guard`` delegates to this function so the temporal
    checks have one implementation. Non-temporal checks (imports, nested loops,
    recursion, unknown columns, large ranges) are NOT returned here.

    Ambiguous look-ahead constructs are treated as a hard reject (the
    faithfulness/honesty posture of the leakage stage).
    """

    issues: list[GuardIssue] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [
            GuardIssue(
                code="syntax_error",
                message=str(exc),
                location=f"line {exc.lineno}" if exc.lineno else None,
            )
        ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _check_temporal_call(node, issues)
        elif isinstance(node, ast.Subscript):
            _check_forward_indexing(node, issues)

    text = re.sub(r"\s+", "", code)
    for pattern in FORBIDDEN_TIME_ORDER_PATTERNS:
        if pattern in text:
            issues.append(
                GuardIssue(
                    code="possible_reverse_time_order",
                    message=f"Forbidden time-order pattern detected: {pattern}",
                )
            )

    return issues


def run_static_alpha_code_guard(code: str, function_name: str | None = None) -> GuardReport:
    """Run syntax, import, leakage, and shape checks that do not execute code."""

    issues: list[GuardIssue] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return GuardReport(
            guard_name="static_alpha_code",
            status=GuardStatus.FAIL,
            issues=[
                GuardIssue(
                    code="syntax_error",
                    message=str(exc),
                    location=f"line {exc.lineno}" if exc.lineno else None,
                )
            ],
        )

    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1:
        issues.append(
            GuardIssue(
                code="function_count",
                message="Alpha code must define exactly one top-level function.",
            )
        )
    elif function_name and functions[0].name != function_name:
        issues.append(
            GuardIssue(
                code="function_name_mismatch",
                message=f"Expected function {function_name!r}, found {functions[0].name!r}.",
                location=f"line {functions[0].lineno}",
            )
        )

    function = functions[0] if len(functions) == 1 else None
    if function is not None:
        _check_recursion(function, issues)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _check_import(node, issues)
        elif isinstance(node, ast.Call):
            _check_call(node, issues)
        elif isinstance(node, ast.Subscript):
            _check_subscript(node, issues)
        elif isinstance(node, (ast.For, ast.While)):
            _check_loop(node, issues)

    # Merge the factored-out temporal-leakage subset (single source for temporal
    # checks; D-04). syntax_error cannot occur here -- the umbrella guard already
    # parsed the code above and returned early on a SyntaxError.
    issues.extend(run_temporal_leakage_static_scan(code, function_name))

    status = (
        GuardStatus.FAIL
        if any(issue.severity == "error" for issue in issues)
        else GuardStatus.PASS
    )
    return GuardReport(guard_name="static_alpha_code", status=status, issues=issues)


def _check_import(node: ast.Import | ast.ImportFrom, issues: list[GuardIssue]) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORT_MODULES:
                issues.append(
                    GuardIssue(
                        code="forbidden_import",
                        message=f"Import {alias.name!r} is outside the alpha runtime allowlist.",
                        location=f"line {node.lineno}",
                    )
                )
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        root = module.split(".")[0]
        if root not in ALLOWED_IMPORT_MODULES:
            issues.append(
                GuardIssue(
                    code="forbidden_import",
                    message=f"Import from {module!r} is outside the alpha runtime allowlist.",
                    location=f"line {node.lineno}",
                )
            )


def _check_call(node: ast.Call, issues: list[GuardIssue]) -> None:
    name = _call_name(node.func)
    if name in FORBIDDEN_ALPHA_CALLS:
        issues.append(
            GuardIssue(
                code="forbidden_call",
                message=f"Forbidden call {name!r} detected.",
                location=f"line {node.lineno}",
            )
        )

    if name == "range":
        range_size = _literal_range_size(node)
        if range_size is not None and range_size > MAX_STATIC_RANGE_SIZE:
            issues.append(
                GuardIssue(
                    code="large_range",
                    message=(
                        f"Literal range size {range_size} exceeds static guard limit "
                        f"{MAX_STATIC_RANGE_SIZE}."
                    ),
                    location=f"line {node.lineno}",
                )
            )


def _check_temporal_call(node: ast.Call, issues: list[GuardIssue]) -> None:
    """Temporal-only call checks: negative-period shift/diff/pct_change + rolling."""

    name = _call_name(node.func)

    for method in _NEGATIVE_PERIOD_METHODS:
        if name.endswith(f".{method}") and _has_negative_period(node):
            issues.append(
                GuardIssue(
                    code="future_shift",
                    message=(
                        f"Negative period on .{method}(...) reaches into future rows "
                        "and is forbidden."
                    ),
                    location=f"line {node.lineno}",
                )
            )
            break

    if name.endswith(".rolling"):
        for keyword in node.keywords:
            if (
                keyword.arg == "center"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                issues.append(
                    GuardIssue(
                        code="centered_rolling_window",
                        message=(
                            "Centered rolling windows use future observations "
                            "and are forbidden."
                        ),
                        location=f"line {node.lineno}",
                    )
                )


def _has_negative_period(node: ast.Call) -> bool:
    """Return whether a shift/diff/pct_change call has a negative period.

    Inspects BOTH the first positional argument and a ``periods=`` keyword (the
    keyword form was previously missed). ``.shift`` / ``.diff`` / ``.pct_change``
    all use ``periods`` as the first parameter.
    """

    if node.args and _is_negative_number(node.args[0]):
        return True
    return any(
        keyword.arg == "periods" and _is_negative_number(keyword.value)
        for keyword in node.keywords
    )


def _check_forward_indexing(node: ast.Subscript, issues: list[GuardIssue]) -> None:
    """Flag absolute-future label indexing: ``df.loc[<non-slice constant>]``.

    ``.loc`` with a single bare constant key (not a slice / not a column access)
    is an absolute row selection that can reach a future date; ambiguous cases are
    treated as a hard reject (faithfulness posture, D-04).
    """

    value = node.value
    if not (isinstance(value, ast.Attribute) and value.attr == "loc"):
        return
    key = node.slice
    if isinstance(key, ast.Slice):
        return
    # df.loc[<int/float constant>] -- positional-looking absolute future index.
    if isinstance(key, ast.Constant) and isinstance(key.value, (int, float)):
        issues.append(
            GuardIssue(
                code="forward_indexing",
                message=(
                    "Absolute .loc[...] indexing with a constant key may reach "
                    "future rows and is forbidden."
                ),
                location=f"line {node.lineno}",
            )
        )


def _check_subscript(node: ast.Subscript, issues: list[GuardIssue]) -> None:
    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        column = node.slice.value
        if _looks_like_ohlcv_access(node) and not _is_known_dataframe_column(column):
            issues.append(
                GuardIssue(
                    code="unknown_input_column",
                    message=f"Column {column!r} is outside the OHLCV Input contract.",
                    location=f"line {node.lineno}",
                )
            )


def _check_loop(node: ast.For | ast.While, issues: list[GuardIssue]) -> None:
    if _contains_nested_loop(node):
        issues.append(
            GuardIssue(
                code="nested_loop",
                message="Nested loops are forbidden in generated Alpha Functions.",
                location=f"line {node.lineno}",
            )
        )
    if isinstance(node, ast.While) and not _is_bounded_while_condition(node.test):
        issues.append(
            GuardIssue(
                code="unbounded_loop",
                message=(
                    "While loops must have a simple statically bounded comparison "
                    "against len(df) or a literal."
                ),
                location=f"line {node.lineno}",
            )
        )


def _check_recursion(function: ast.FunctionDef, issues: list[GuardIssue]) -> None:
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and _call_name(node.func) == function.name:
            issues.append(
                GuardIssue(
                    code="recursion",
                    message="Recursive Alpha Functions are forbidden.",
                    location=f"line {node.lineno}",
                )
            )
            return


def _looks_like_ohlcv_access(node: ast.Subscript) -> bool:
    value = node.value
    return isinstance(value, ast.Name) and value.id in {"df", "df_copy"}


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parent = _call_name(func.value)
        return f"{parent}.{func.attr}" if parent else func.attr
    if isinstance(func, ast.Subscript):
        return _call_name(func.value)
    return ""


def _is_known_dataframe_column(column: str) -> bool:
    return is_ohlcv_or_factor_column(column)


def _is_negative_number(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value < 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return isinstance(node.operand, ast.Constant) and isinstance(
            node.operand.value, (int, float)
        )
    return False


def _contains_nested_loop(node: ast.For | ast.While) -> bool:
    for statement in node.body:
        for descendant in ast.walk(statement):
            if isinstance(descendant, (ast.For, ast.While)):
                return True
    return False


def _is_bounded_while_condition(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not isinstance(node.ops[0], (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        return False
    left = node.left
    right = node.comparators[0]
    return _is_literal_int(left) or _is_literal_int(right) or _is_len_df(left) or _is_len_df(right)


def _literal_range_size(node: ast.Call) -> int | None:
    values = [_literal_int(argument) for argument in node.args]
    if not values or any(value is None for value in values):
        return None
    if len(values) == 1:
        return abs(values[0] or 0)
    start = values[0] or 0
    stop = values[1] or 0
    step = abs(values[2] or 1) if len(values) >= 3 else 1
    return abs(stop - start) // max(step, 1)


def _literal_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_int(node.operand)
        return -value if value is not None else None
    return None


def _is_literal_int(node: ast.AST) -> bool:
    return _literal_int(node) is not None


def _is_len_df(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and _call_name(node.func) == "len"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "df"
    )
