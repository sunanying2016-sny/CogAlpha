"""Minimal command-line entry for the v4.0 deterministic orchestrator (D-02).

Builds a reduced-config ``ProtocolConfig.validation`` profile, wires the REAL
Wave-3 stage bundle (``make_stage_bundle`` — generation + quality + evolution +
injection + fitness, 19-03/04/05) backed by a deterministic offline invoker +
metrics provider, and drives the §2 loop, printing the ``RunResult`` so the engine
is visibly command-line runnable with the real stages — a structural dry-run ahead
of the Phase 22 real-LLM acceptance gate.

The profile ``generations=6 == inner_subcycles 2 * subcycle_length 3`` clears the
validation floors (generations>=4 / inner_subcycles>=2) and makes the run execute
6 generations with §3.5 adaptive feedback built every gen and §4.1 injection firing
exactly twice (at g=1 and g=3; g=5 breaks before injection). ``injection_every``
stays paper-pinned at 2 — the 2x firing comes from the generation count, not from
overriding the cadence. The real bundle is built from THIS protocol so the wired
stages' ``factors_per_request`` / ``children_pool`` / minima bounds match the run.

Durable checkpoint / resume (CONC-06 / 20-05): ``--checkpoint-dir`` selects where
the per-generation ``gen-<g>.json`` snapshots land (default ``outputs/checkpoints``);
``--resume`` loads the latest snapshot, validates its run identity against the current
protocol (refusing a mismatch — Pitfall 5), and continues deterministically via
``run(..., state=, start_generation=)``.

Run summary (BACK-04 / Phase 21): after the §2 loop returns its ``RunResult``, the
entry point writes a run summary via ``cogalpha.io.run`` (``summarize_cogalpha_run``
-> ``write_json`` under ``resolve_run_dir``). The finalize (combination -> backtest)
seam is gated behind the offline-vs-real condition: the offline dry-run drives the
deterministic offline bundle with NO market panel, so it writes a real *run-portion*
summary (the ``run`` block populated; ``combined_signal`` / ``backtest`` ``None``)
WITHOUT fabricating a backtest from no panel.

Real-LLM acceptance run (VAL-01 / Phase 22): ``--real`` switches to the live wiring
branch. It loads the secret ``COGALPHA_LLM_API_KEY`` (KEY.md / env), builds a real
``RecordingInvoker(SkillInvoker(StandardSkillLoader, OpenAICompatibleClient.from_env))``
+ ``PanelBackedMetricsProvider.from_split`` over the prepared CSI300 ``.test`` split +
an ``AlphaExecPool``, and wires them through the SAME ``make_stage_bundle`` / ``run``
path as the dry-run — sliced to ``agent_specs=DOMAIN_AGENT_SPECS[:domain_agents]`` (the
cost lever) and ``require_pool=True`` (the fail-closed live boundary). After the loop it
runs ``train_combination_signal`` over the elite-pool factor values + the test-split
label and ``orchestrator.finalize`` over that combined signal, feeding the non-None
combined-signal / AER-IR / IC-family into the same ``summarize_cogalpha_run`` writer.
``--max-invocations`` caps spend (first-class ``budget_exhausted`` partial). The actual
PAID launch is gated behind a blocking human checkpoint (plan 22-01 Task 3).
``--output-root`` / ``--run-id`` select where the ``run-summary.json`` lands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cogalpha.artifacts import (
    DurableCheckpointWriter,
    build_run_identity,
    latest_checkpoint,
    validate_checkpoint_identity,
)
from cogalpha.io.run import (
    configure_llm_provider,
    load_key_file,
    resolve_run_dir,
    summarize_cogalpha_run,
    write_json,
)
from cogalpha.orchestrator import RunResult, RunStatus, make_stage_bundle, run
from cogalpha.protocol import ProtocolConfig
from cogalpha.skill_runtime.registry import DOMAIN_AGENT_SPECS
from cogalpha.stages.defaults import (
    _DeterministicMetricsProvider,
    _FakeInvoker,
)

if TYPE_CHECKING:
    import pandas as pd

    from cogalpha.schemas import AlphaCandidate
    from cogalpha.state import CogAlphaState

_DEFAULT_CHECKPOINT_DIR = "outputs/checkpoints"
_DEFAULT_OUTPUT_ROOT = "outputs/runs"
_DEFAULT_RUN_ID = "dry-run"
_RUN_SUMMARY_FILENAME = "run-summary.json"
_DEFAULT_PROVIDER = "deepseek"
_DEFAULT_DATA_DIR = "data/processed/direct_qlib_csi300_u100"
_KEY_FILE = "KEY.md"
# The single live untrusted-exec timeout budget for materializing elite factor
# values through the AlphaExecPool (seconds per candidate batch).
_FACTOR_EXEC_TIMEOUT_SECONDS = 60.0


def _build_protocol() -> ProtocolConfig:
    return ProtocolConfig.validation(
        domain_agents=2,
        initial_pool=4,
        parent_pool=2,
        children_pool=6,
        generations=6,
        inner_subcycles=2,
        subcycle_length=3,
    )


def _build_real_protocol() -> ProtocolConfig:
    """D-1 representative-mid validation profile for the real-LLM acceptance run.

    ``domain_agents=6`` / ``initial_pool=24`` sit inside the 5–7 / 20–40 bands; the
    cadence (``generations=6 == inner_subcycles 2 * subcycle_length 3``) clears the
    validation floors and fires §4.1 injection twice (g=1, g=3). ``parent_pool=8`` =>
    ``children_pool=24`` (paper-pinned 3x). ``factors_per_request`` defaults to 4 so
    ``4 * 6 = 24 >= initial_pool``.
    """

    return ProtocolConfig.validation(
        domain_agents=6,
        initial_pool=24,
        parent_pool=8,
        children_pool=24,
        generations=6,
        inner_subcycles=2,
        subcycle_length=3,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the v4.0 deterministic orchestrator dry-run with durable "
            "per-generation checkpoint / resume (CONC-06)."
        )
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the latest gen-<g>.json checkpoint in --checkpoint-dir.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=_DEFAULT_CHECKPOINT_DIR,
        help=(
            "Directory holding the per-generation gen-<g>.json snapshots "
            f"(default: {_DEFAULT_CHECKPOINT_DIR})."
        ),
    )
    parser.add_argument(
        "--output-root",
        default=_DEFAULT_OUTPUT_ROOT,
        help=(
            "Root directory for run artifacts; the run summary lands under "
            f"<output-root>/<run-id>/{_RUN_SUMMARY_FILENAME} "
            f"(default: {_DEFAULT_OUTPUT_ROOT})."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=_DEFAULT_RUN_ID,
        help=(
            "Single-component run id naming the artifact subdirectory "
            f"(default: {_DEFAULT_RUN_ID})."
        ),
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Run the PAID real-LLM acceptance branch (VAL-01): real invoker + "
            "real CSI300 panel + AlphaExecPool through the same make_stage_bundle "
            "path, with finalize-with-panel. Gated behind a blocking human "
            "checkpoint; cap spend with --max-invocations."
        ),
    )
    parser.add_argument(
        "--provider",
        default=_DEFAULT_PROVIDER,
        help=(
            "LLM provider profile for the real branch "
            f"(default: {_DEFAULT_PROVIDER})."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=_DEFAULT_DATA_DIR,
        help=(
            "Prepared CSI300 market-data directory; the real branch loads its "
            f"`.test` split (default: {_DEFAULT_DATA_DIR})."
        ),
    )
    parser.add_argument(
        "--max-invocations",
        type=int,
        default=None,
        help=(
            "Hard cost ceiling for the real run: stops with a first-class "
            "budget_exhausted partial once this many skill invocations are charged."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "Runtime dispatch parallelism for the real branch: bounds the LLM "
            "fan-out (generation/quality/evolution/injection) + factor-exec pool + "
            "fitness scoring. I/O-bound LLM calls benefit from a high value (e.g. 20). "
            "concurrency=1 is byte-for-byte the serial path (default)."
        ),
    )
    # LLM-config overrides consumed by configure_llm_provider on the real branch.
    parser.add_argument("--model", default=None, help="Override COGALPHA_LLM_MODEL.")
    parser.add_argument(
        "--base-url", default=None, help="Override COGALPHA_LLM_BASE_URL."
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Override COGALPHA_LLM_REASONING_EFFORT.",
    )
    parser.add_argument(
        "--thinking", default=None, help="Override COGALPHA_LLM_THINKING."
    )
    parser.add_argument(
        "--max-tokens", type=int, default=None, help="Override COGALPHA_LLM_MAX_TOKENS."
    )
    return parser.parse_args(argv)


def run_cli(argv: list[str] | None = None) -> RunResult:
    """Drive the §2 loop with durable checkpoint / resume; return the ``RunResult``.

    A fresh run writes ``gen-<g>.json`` snapshots into ``--checkpoint-dir`` via the
    durable atomic writer. ``--resume`` loads the latest snapshot, validates its run
    identity against the current protocol (refusing a mismatch — Pitfall 5), and
    continues deterministically from ``g+1`` (resume == uninterrupted, D-02a).
    """

    args = _parse_args(argv)
    if args.real:
        return _run_real(args)

    protocol = _build_protocol()
    checkpoint_dir = Path(args.checkpoint_dir)
    current_identity = build_run_identity(protocol)

    stages = make_stage_bundle(
        protocol,
        _FakeInvoker(),
        _DeterministicMetricsProvider(),
    )
    checkpoint_writer = DurableCheckpointWriter(
        checkpoint_dir, run_identity=current_identity
    )

    if args.resume:
        generation, loaded_state = _resolve_resume(checkpoint_dir, current_identity)
        result = run(
            protocol,
            stages=stages,
            checkpoint_writer=checkpoint_writer,
            state=loaded_state,
            start_generation=generation,
        )
    else:
        result = run(
            protocol,
            stages=stages,
            checkpoint_writer=checkpoint_writer,
        )

    _write_run_summary(result, args.output_root, args.run_id)
    return result


def _write_run_summary(
    result: RunResult,
    output_root: str,
    run_id: str,
) -> Path:
    """Persist the run summary via ``cogalpha.io.run`` (BACK-04, behavioral pin).

    Offline-vs-real gating: this deterministic dry-run has NO market panel, so the
    finalize (combination -> backtest) seam is NOT invoked here — we write the
    *run-portion* summary (the ``run`` block populated; ``combined_signal`` /
    ``backtest`` ``None``) without fabricating a backtest from no panel. The real-LLM
    run with a panel feeds ``orchestrator.finalize`` over the 21-02 combined signal
    and the 21-01 AER/IR/IC-family into the same ``summarize_cogalpha_run`` writer
    (Phase 22). The offline path always ends with a NON-EMPTY run-summary JSON.
    """

    run_dir = resolve_run_dir(output_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_cogalpha_run(
        result,
        # The offline dry-run collects no per-gen CandidateEvaluationResult evidence,
        # produces no combined signal, and runs no backtest (no panel). The summary
        # is the real run-portion; the metric aggregations are wired and exercised by
        # the io/run.py unit tests and become populated on the Phase-22 real run.
        [],
        combined_signal=None,
        backtest_metrics=None,
        ic_family=None,
    )
    summary_path = run_dir / _RUN_SUMMARY_FILENAME
    write_json(summary_path, summary)
    return summary_path


def _read_checkpoint_identity(checkpoint_dir: Path, generation: int) -> dict:
    payload = json.loads(
        (checkpoint_dir / f"gen-{generation}.json").read_text(encoding="utf-8")
    )
    return payload["identity"]


def _resolve_resume(
    checkpoint_dir: Path,
    current_identity: dict,
) -> tuple[int, CogAlphaState]:
    """Load + validate the latest checkpoint for a ``--resume`` run (single source).

    Shared by the offline and the ``--real`` branches. Returns ``(start_generation,
    state)`` to thread into ``run(...)``. Refuses a missing checkpoint and (Pitfall 5) a
    protocol-mismatched one.

    WR-01 gen-0 guard: ``run()`` treats ``start_generation == 0`` as a FRESH run
    (``resume_after = start_generation if start_generation > 0 else -1``), so a lone
    ``gen-0`` checkpoint cannot be unambiguously resumed — passing it would re-execute
    generation 0 (re-paying LLM calls on ``--real``). Refuse it with an actionable
    message instead of silently re-running; a fresh run from gen 0 is the correct recovery.
    """

    loaded = latest_checkpoint(checkpoint_dir)
    if loaded is None:
        raise ValueError(
            f"no checkpoint to resume from in {checkpoint_dir!s}; run without "
            "--resume first to produce gen-<g>.json snapshots"
        )
    generation, state = loaded
    validate_checkpoint_identity(
        _read_checkpoint_identity(checkpoint_dir, generation), current_identity
    )
    if generation == 0:
        raise ValueError(
            "cannot --resume from a lone gen-0 checkpoint: run()'s start_generation=0 "
            "means a fresh run, so generation 0 would re-execute (re-paying LLM calls on "
            "--real). Re-run without --resume instead."
        )
    return generation, state


# ---------------------------------------------------------------------------
# Real-LLM acceptance branch (VAL-01 / Phase 22)
# ---------------------------------------------------------------------------


def _build_real_invoker(run_dir: Path):
    """Build the real recording invoker over the live OpenAI-compatible client.

    ``RecordingInvoker(SkillInvoker(StandardSkillLoader, OpenAICompatibleClient
    .from_env()))`` — the recorder writes per-call evidence as ``request_sha256``
    (NEVER the raw secret) to ``run_dir/skill_invocations.jsonl``.
    """

    from cogalpha.instrumentation import InvocationRecorder, RecordingInvoker
    from cogalpha.llm.client import OpenAICompatibleClient
    from cogalpha.skill_runtime.invocation import SkillInvoker
    from cogalpha.skill_runtime.loader import StandardSkillLoader
    from cogalpha.skill_runtime.registry import SKILLS_ROOT

    inner = SkillInvoker(
        loader=StandardSkillLoader(SKILLS_ROOT),
        client=OpenAICompatibleClient.from_env(),
    )
    return RecordingInvoker(
        inner=inner,
        recorder=InvocationRecorder(run_dir / "skill_invocations.jsonl"),
        context_variant="real",
    )


def _elite_feature_matrix(
    state,
    pool,
) -> pd.DataFrame:
    """Materialize the elite-pool factor values into a ``(date,ticker)`` feature frame.

    Each elite candidate's untrusted factor code is executed through the SAME
    ``AlphaExecPool`` (process isolation, fail-closed) used by fitness; the resulting
    ``(date,ticker)`` factor series become one column each (keyed by candidate id),
    so ``train_combination_signal`` sees one column per elite factor.
    """

    import pandas as pd

    from cogalpha.evaluation import run_alpha_code_via_pool

    elites: list[AlphaCandidate] = [
        state.store[candidate_id]
        for candidate_id in state.elite_pool
        if candidate_id in state.store
    ]
    if not elites:
        raise ValueError(
            "real run produced an empty elite pool; cannot build a combination "
            "feature matrix (the run did not converge to any elite factor)"
        )
    exec_results = run_alpha_code_via_pool(
        pool, elites, timeout_seconds=_FACTOR_EXEC_TIMEOUT_SECONDS
    )
    columns: dict[str, pd.Series] = {}
    for candidate, result in zip(elites, exec_results, strict=True):
        if result.ok and result.factor_values is not None:
            columns[candidate.candidate_id] = result.factor_values
    if not columns:
        raise ValueError(
            "no elite factor produced executable values through the pool; cannot "
            "train a combination signal"
        )
    return pd.DataFrame(columns)


def _run_real(args: argparse.Namespace) -> RunResult:
    """The PAID real-LLM acceptance branch (VAL-01).

    Wires the real invoker / metrics provider / exec pool through the SAME
    ``make_stage_bundle`` + ``run`` path as the dry-run, sliced to
    ``agent_specs=DOMAIN_AGENT_SPECS[:domain_agents]`` (the cost lever) and
    ``require_pool=True`` (the fail-closed live boundary). After the loop it runs the
    reduced-config finalize chain (``train_combination_signal`` ->
    ``orchestrator.finalize``) and writes a NON-None combined-signal / backtest /
    IC-family summary. ``--max-invocations`` caps spend.
    """

    from cogalpha.benchmark.presets import COGALPHA_CSI300_OHLCV_V1
    from cogalpha.combination import CombinationConfig, train_combination_signal
    from cogalpha.data import load_prepared_baseline_market_data
    from cogalpha.evaluation import EvaluationCache, PanelBackedMetricsProvider
    from cogalpha.execution_pool import AlphaExecPool
    from cogalpha.orchestrator import finalize

    load_key_file(_KEY_FILE)
    configure_llm_provider(args)

    protocol = _build_real_protocol()
    run_dir = resolve_run_dir(args.output_root, args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = Path(args.checkpoint_dir)
    current_identity = build_run_identity(protocol)

    market = load_prepared_baseline_market_data(args.data_dir)
    split = market.test
    # split.forward_returns is a wide (date rows x ticker columns) DataFrame; both
    # train_combination_signal's label contract and its internal (date,ticker)
    # boolean-mask slicing need the long MultiIndex-Series shape instead.
    label = split.forward_returns.stack(future_stack=True)

    import pandas as pd

    benchmark_returns_path = Path(args.data_dir) / "benchmark_returns.parquet"
    benchmark_returns = pd.read_parquet(benchmark_returns_path).iloc[:, 0]

    pool = AlphaExecPool(split.ohlcv_panel, concurrency=args.concurrency)
    try:
        invoker = _build_real_invoker(run_dir)
        provider = PanelBackedMetricsProvider.from_split(
            split,
            cache=EvaluationCache(run_dir / "eval.jsonl"),
            exec_pool=pool,
            concurrency=args.concurrency,
        )
        stages = make_stage_bundle(
            protocol,
            invoker,
            provider,
            runtime_ohlcv_panel=split.ohlcv_panel,
            exec_pool=pool,
            require_pool=True,
            agent_specs=DOMAIN_AGENT_SPECS[: protocol.domain_agents],
            concurrency=args.concurrency,
        )
        checkpoint_writer = DurableCheckpointWriter(
            checkpoint_dir, run_identity=current_identity
        )
        # --resume continues from the latest gen-<g>.json snapshot (Pitfall 5: refuse a
        # protocol-mismatched checkpoint). Resuming the FINAL generation re-runs only the
        # no-LLM fitness/break tail + finalize, recovering a run whose loop completed but
        # whose post-loop finalize failed — without re-paying for the LLM generations.
        resume_state = None
        resume_generation = 0
        if args.resume:
            resume_generation, resume_state = _resolve_resume(
                checkpoint_dir, current_identity
            )
        result = run(
            protocol,
            stages=stages,
            checkpoint_writer=checkpoint_writer,
            state=resume_state,
            start_generation=resume_generation,
            max_invocations=args.max_invocations,
        )

        # Finalize-with-panel only when the run COMPLETED and converged to elite factors.
        # WR-02 cost guard: a budget_exhausted / interrupted / partial run must NOT enter
        # finalize — `_elite_feature_matrix` executes untrusted factor code per elite
        # through the pool, which would escape the `--max-invocations` spend ceiling. An
        # empty elite pool (no factor cleared the App A.4 fitness minima) is likewise a
        # LEGITIMATE reduced-scale outcome, NOT a crash: write the run-portion summary with
        # a null backtest (mirroring the offline no-panel path). S5 (real backtest) is then
        # honestly unmet, deferred to a higher-scale run.
        combined_signal = None
        backtest_metrics: dict | None = None
        if result.status != RunStatus.COMPLETED:
            print(
                f"WARNING: run did not complete (status={result.status.value}); skipping "
                "finalize/backtest to honor the cost ceiling — run-summary backtest=None.",
                file=sys.stderr,
            )
        elif result.final_state.elite_pool:
            try:
                features = _elite_feature_matrix(result.final_state, pool)
                combined_signal = train_combination_signal(
                    features, label, CombinationConfig()
                )
                final = finalize(
                    result.final_state,
                    COGALPHA_CSI300_OHLCV_V1,
                    split.ohlcv_panel,
                    label,
                    combined_signal=combined_signal,
                    benchmark_returns=benchmark_returns,
                )
                backtest_metrics = final.model_dump()
            except ValueError as exc:  # elites present but none produced executable values
                print(
                    f"WARNING: finalize skipped ({exc}); writing run-summary with "
                    "backtest=None.",
                    file=sys.stderr,
                )
                combined_signal = None
        else:
            print(
                "WARNING: real run produced an empty elite pool — no factor cleared the "
                "fitness gate; writing run-summary with backtest=None (S5 unmet, deferred "
                "to a higher-scale run).",
                file=sys.stderr,
            )
    finally:
        pool.close()

    summary = summarize_cogalpha_run(
        result,
        [],
        combined_signal=combined_signal,
        backtest_metrics=backtest_metrics,
        ic_family=None,
    )
    write_json(run_dir / _RUN_SUMMARY_FILENAME, summary)
    return result


def main(argv: list[str] | None = None) -> None:
    result = run_cli(argv)
    print(
        result.status,
        result.generations_completed,
        "/",
        result.generations_target,
    )


if __name__ == "__main__":
    main()
