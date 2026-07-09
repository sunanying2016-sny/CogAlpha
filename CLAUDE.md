# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## Project overview

CogAlpha is a **skill-driven reproduction of "Cognitive Alpha Mining"** — an LLM-multi-agent
system that generates OHLCV-only stock alpha factors, filters them through deterministic
quality/leakage/statistical gates, and (only when enough factors survive) trains a combined
signal and runs a top-50/drop-5 portfolio backtest. It is explicitly a **benchmark-first
research runtime**, not a repository that claims a discovered alpha is effective — the public
README's central message is "don't trust a factor until artifact evidence supports it."

The repo is `pyproject.toml`-named `cogalpha`, version `0.1.0`, Python `>=3.11` (pinned to
`3.12` via `.python-version`). Package manager is `uv` (there's a committed `uv.lock`).
`git remote`: `https://github.com/AI3-GenAI4Sci/CogAlpha`, currently on `main`
(`c163b0c release: publish backtest walkthrough`); a `dev-0.0.1` branch also exists.

**This is a public "runtime release" branch, not the full research repo.** `data/` is an empty
placeholder (`.gitkeep` only — real market data is prepared locally and gitignored), there is
**no `tests/` directory** despite `pyproject.toml` declaring `testpaths = ["tests"]` and a
`perf` pytest marker (`tests/test_hot_path_scaling.py` is referenced in a comment but does not
exist in this checkout), and `outputs/`, `logs/`, `.env`, `KEY.md` are all gitignored. Don't be
surprised that `pytest` finds nothing to collect — that's expected for this branch, not a bug.

Extensive internal design-decision code comments reference a paper section numbering scheme
(`§2`, `§3.1`–`§3.6`, `§4.1`, `App A.2`–`A.4`, `App B.4`, `App C.1`) and a project-internal
finding/decision numbering (`D-01`, `PROTO-04`, `STAGE-0x`, `BACK-0x`, `CONC-0x`, `WR-0x`,
`T-xx-yy`, phase numbers like "19-03"/"20-05"/"Phase 21"/"Phase 22"). These are load-bearing
comments left by the prior implementation effort — read them; they explain *why* code is
shaped the way it is (e.g. why a stage returns `None` instead of an empty result, why a pool
is required on the live path, why a percentile threshold has both a relative and absolute
floor). Treat "the paper" in these comments as the CogAlpha/Cognitive-Alpha-Mining paper this
project reproduces, not any other paper in the codebase's orbit (`QuantaAlpha` is a sibling
project referenced only as a comparison benchmark preset — see below).

## Relationship to QuantaAlpha

`cogalpha/benchmark/presets.py` defines **two** benchmark presets side by side:
`cogalpha_csi300_ohlcv_v1` (this project's own paper-stated CSI300 setting: 2011-01-01 to
2024-12-01, 10-day next-**open** forward return, `entry_delay_days=1`) and
`quantaalpha_csi300_ohlcv_v1` (a snapshot of `QuantaAlpha`'s public `configs/backtest.yaml`
config captured 2026-06-06 for comparison only: 2016-01-01 to 2025-12-26, next-day **close**
expression `Ref($close, -2) / Ref($close, -1) - 1`). These are **deliberately never mixed** —
different date windows, different label definitions, different horizons. If you're asked to
compare CogAlpha's and QuantaAlpha's numbers, the date windows and labels are NOT compatible
without explicit reconciliation; the code comments call this out as a hard invariant
("CogAlpha and QuantaAlpha settings must stay separate").

## Setup

```bash
uv sync --python 3.12 --extra dev          # base install
uv sync --python 3.12 --extra dev --extra qlib   # + pyqlib (only if you need Direct-Qlib data prep; pyqlib requires python<3.13)
```

Real-LLM mode needs `COGALPHA_LLM_API_KEY` in the shell env (or a local, gitignored `KEY.md`
loaded via `cogalpha.io.run.load_key_file` — never committed). Never write real key values
into README/artifacts/git.

## Common commands

```bash
# Dry-run: no LLM calls, deterministic fake invoker + fake metrics provider,
# exercises the REAL orchestrator/stage wiring end-to-end. Writes
# outputs/runs/dry-run/run-summary.json and outputs/checkpoints/gen-*.json.
uv run --python 3.12 --extra dev python scripts/run.py

uv run --python 3.12 --extra dev python scripts/run.py --help

# Real LLM + real market data (PAID). Requires prepared data under --data-dir
# (see "Data preparation" below) and COGALPHA_LLM_API_KEY set.
uv run --python 3.12 --extra dev python scripts/run.py \
  --real --data-dir data/processed/direct_qlib_csi300 \
  --output-root outputs/runs --run-id my-run \
  --checkpoint-dir outputs/checkpoints/my-run \
  --max-invocations 261 --concurrency 20

# Resume from the latest checkpoint (refuses a protocol/benchmark mismatch):
uv run --python 3.12 --extra dev python scripts/run.py --resume --checkpoint-dir outputs/checkpoints/my-run
```

**Data preparation** (`scripts/prepare_direct_qlib_csi300.py`, needs a local Qlib
`provider_uri`, e.g. `~/.qlib/qlib_data/cn_data`):

```bash
uv run --python 3.12 --extra dev --extra qlib python scripts/prepare_direct_qlib_csi300.py \
  --provider-uri ~/.qlib/qlib_data/cn_data --market csi300 --benchmark-symbol SH000300 \
  --output-dir data/processed/direct_qlib_csi300 --start-date 2011-01-01 --end-date 2024-12-01 --strict
```
This only exports the merged OHLCV panel + source manifest. `train/valid/test` splits and the
forward-return label still need `cogalpha.data.build_baseline_market_data` run once over the
exported panel (see README "准备 Direct-Qlib 数据" for the exact snippet) before
`load_prepared_baseline_market_data` (used by `--real`) will find `train_ohlcv.parquet` etc.

There are also `scripts/prepare_hf_qlib_csi300.py` (loads the `QuantaAlpha/qlib_csi300`
Hugging Face HDF5 dump via `cogalpha.data.load_qlib_daily_pv_hdf`) and
`scripts/prepare_mini_csi300.py` (small synthetic/reduced dataset for fast local iteration) as
alternative data sources — same downstream `build_baseline_market_data` contract applies.

**No test suite ships in this branch.** Don't go looking for one; verification here means
running `scripts/run.py` (dry-run first) and reading the `run-summary.json` / checkpoint JSON.

## Architecture

### The paper §2 loop, end to end

`cogalpha/orchestrator.py::run()` is the single deterministic outer loop. Cadence is
**entirely sourced from `ProtocolConfig`** — there is no hardcoded loop bound anywhere on this
path (a static check enforced this during development, referenced as "VAL-02" in comments).

```
for subcycle in range(inner_subcycles):        # e.g. 3 (paper) / 2 (validation default)
    if new subcycle: adaptive re-seed (generation stage + quality stage)
    for gen in range(subcycle_length):          # e.g. 8 (paper) / 3 (validation default)
        g = subcycle * subcycle_length + gen
        fitness_stage(state)                    # §3.4 — partitions candidate_pool in place, returns None
        if g == generations - 1: break          # fixed generation-count termination (NEVER an empty-pool break)
        generation_stage(state, feedback)        # §3.5 — 21 (or sliced N) domain agents, every generation
        quality_stage(state, feedback)           # §3.3 A.3 six-step check (see below)
        evolution_stage(state, feedback)         # §3.6 — mutation/crossover/crossover→mutation, exactly children_pool children
        quality_stage(state, feedback)
        if (g+1) % injection_every == 0:         # §4.1 — paper-pinned to every 2 generations
            injection_stage(state, feedback)     # same fan-out pattern as generation, but task-agent framing
            quality_stage(state, feedback)
        checkpoint_writer.write(state, g)        # durable gen-<g>.json (see Checkpointing below)
```

After the loop, `orchestrator.finalize()` is a **separate, pure, append-only seam** (never
called from inside `run()`): it takes `state.elite_pool`, trains a combined signal
(`combination.py`), and runs the top-50/drop-5 backtest (`backtest.py`). If `elite_pool` is
empty (very common at small scale — see the walkthrough example below), `finalize` is skipped
entirely and the run summary honestly records `combined_signal: null, backtest: null` — **this
is not a bug**, it's the "don't fabricate a result you don't have" design principle running
throughout this codebase.

### The five pools (`cogalpha/state.py::CogAlphaState`)

A single central `store: dict[candidate_id, AlphaCandidate]` holds every candidate payload ever
seen; the five pools (`candidate_pool`, `qualified_pool`, `parent_pool`, `elite_pool`,
`rejected_pool`) hold **id-references only** (bounded memory across a 24-generation run).
`elite_pool` accumulates across generations (deduped); `parent_pool` each generation =
`dedupe(top elite_carry(=2) elites by composite score + this generation's qualified)`,
truncated to `parent_pool` size.

> **Note:** `cogalpha/schemas.py` also defines a `CogAlphaState` class — that one is explicitly
> a "legacy MVP-loop state, superseded by `state.py:CogAlphaState`, retained until its
> harness/runner consumers are removed." Don't confuse the two; the live orchestrator only uses
> `cogalpha.state.CogAlphaState`.

### The A.3 quality sequence (`cogalpha/stages/quality.py`)

Every generated/evolved/injected candidate runs through, in this exact order, until it either
clears all six steps or is hard-rejected to `rejected_pool` (quality is a real filter — it both
records the reject AND prunes the id out of `candidate_pool`, so fitness never scores rejects):

1. **Code Quality** (LLM verdict: accept/repair/reject)
2. **Code Repair** (LLM, retried up to `protocol.quality_repair_attempts`, default 3; unfixable → reject)
3. **Judge** (LLM verdict)
4. **Logic Improvement** (LLM, only on a Judge REPAIR verdict)
5. **Execution & Numerical Stability** — deterministic: executes the candidate's Python
   against a real OHLCV sample, rejects on inf/all-NaN/too-many-NaN (`nan_reject_threshold`,
   paper-pinned 0.30)/non-numeric output
6. **Temporal Leakage** (`cogalpha/stages/leakage.py`) — **hard reject, never a warning**. Two
   independent layers: a static AST scan (forward `shift(-k)`/`diff`/`pct_change`, centered
   rolling windows, absolute `.loc[<const>]` indexing, reverse time-order patterns) PLUS a
   deterministic **executed sentinel test**: builds a fixed synthetic 2-ticker/40-day panel,
   perturbs all rows after a cut date to a huge sentinel value, and checks the factor's output
   *before* the cut is unchanged. This catches look-ahead constructions (e.g.
   reverse→diff→reverse) that slip past the static scan.

### Fitness gate (`cogalpha/fitness.py`, `cogalpha/stages/fitness.py`)

Five metrics per candidate: `ic`, `rank_ic`, `icir`, `rank_icir` (all computed from daily
cross-sectional Pearson/rank correlation between factor score and the forward-return label,
then mean/std across days — **not annualized**), and `mi` (mutual information via
`sklearn.feature_selection.mutual_info_regression` over pooled `(factor, return)` samples).

Classification uses **both** a same-generation percentile AND an absolute floor, take the max:

```
qualified_threshold[metric] = max(65th percentile across this generation's candidates, qualified_minimum[metric])
elite_threshold[metric]     = max(80th percentile across this generation's candidates, elite_minimum[metric])
```

A candidate needs **all five metrics** ≥ threshold to qualify/elite (never a single strong
metric). CSI300 minima: qualified `ic/rank_ic≥0.005, icir/rank_icir≥0.05, mi≥0.02`; elite
`ic/rank_ic≥0.01, icir/rank_icir≥0.1, mi≥0.02` (`cogalpha/config.py::FitnessGateConfig`, mirrored
in `ProtocolConfig.per_metric_minima`). An S&P500 preset id would use `mi≥0.012` instead of
`0.02` per App A.4 — everything else identical — but no S&P500 `BenchmarkSpec` preset actually
exists yet in `benchmark/presets.py`, only the literal minima constant
(`protocol.py::_SP500_MI_MINIMUM`) is pre-wired for when one is added.

**A real run at small/validation scale routinely produces `elite_pool = 0`** — see
`docs/system-walkthrough.md`'s worked example: 6 domain agents × 6 generations × 4
factors/request → 216 real LLM-generated candidates, **all** ended up in `rejected_pool`, none
qualified. This is not necessarily a config bug; five simultaneous, same-generation-relative
thresholds are a genuinely high bar at small pool sizes. Don't assume a `0`-elite run means
something is broken — check whether it's plausible given the scale first.

### `ProtocolConfig` — the single source of truth for every system/scale variable (`protocol.py`)

Two named constructors, both go through the *same* structural validator:

- **`ProtocolConfig.paper_default()`** — zero-arg, pins every field to the paper's full scale:
  `domain_agents=21, initial_pool=80, parent_pool=32, children_pool=96, generations=24
  (inner_subcycles=3 × subcycle_length=8), factors_per_request=4`. Cannot be shrunk.
- **`ProtocolConfig.validation(**overrides)`** — the *only* legitimate way to run smaller;
  accepts overrides for absolute counts only (`domain_agents`, `initial_pool`, `parent_pool`,
  `children_pool`, `generations`, `inner_subcycles`, `subcycle_length`,
  `factors_per_request`, `quality_repair_attempts`). Enforces floors so a reduced run still
  exercises multi-generation + injection (generations≥4, inner_subcycles≥2, parent_pool≥2,
  domain_agents≥1).

**Structural/selection fields are pinned in BOTH profiles and reject any override**:
`children_pool == 3 × parent_pool`, `generations == inner_subcycles × subcycle_length`,
`injection_every == 2`, `elite_carry == 2`, `qualified_percentile == 0.65`,
`elite_percentile == 0.80`, `nan_reject_threshold == 0.30`. If you need a different cadence or
threshold "for testing", that's a signal you're fighting the design, not configuring it — these
are meant to be genuinely non-negotiable per the paper.

`scripts/run.py`'s dry-run profile is `ProtocolConfig.validation(domain_agents=2,
initial_pool=4, parent_pool=2, children_pool=6, generations=6, inner_subcycles=2,
subcycle_length=3)`; its `--real` profile is `domain_agents=6, initial_pool=24, parent_pool=8,
children_pool=24`, same generation cadence.

### Domain agents / skills (`cogalpha/skill_runtime/`, `skills/`)

21 domain agents (`skill_runtime/registry.py::DOMAIN_AGENT_SPECS`) organized into a paper
7-level hierarchy (Market Structure & Cycle → Extreme Risk & Fragility → Price-Volume Dynamics
→ Price-Volatility Behavior → Multi-Scale Complexity → Stability & Regime-Gating → Geometric &
Fusion), plus 4 quality-checker skills and 2 evolution-operator skills (mutation, crossover) —
27 `SKILL.md` files total under `skills/`, one directory each, all inheriting a shared
`skills/_base/SKILL.md` template via a `{base_contract}` placeholder (App C.1 contract: every
domain skill overrides only `agent_role`/`factor_type_phrase`/`agent_focus_intro`/
`factor_design_guidance` HTML-comment-delimited fields, the base owns requirements/constraints/
output format). **This is a Claude-Code-plugin-style skill format re-purposed as an LLM prompt
template system** — `SkillMetadata`/`disable-model-invocation`/`user-invocable`/etc. fields in
the frontmatter parser exist because the loader (`skill_runtime/loader.py`) was built to also
double as a general skill-discovery mechanism, but in this codebase every skill is invoked
programmatically by the orchestrator stages, never by a human typing a slash command.

**Diversified Guidance** (App A.2): each domain agent's prompt gets one of 5 rewording modes
(`light/moderate/creative/divergent/concrete`) injected as a `{guidance_directive}` sentence —
**prompt-level variation only, never a code branch** — via a deterministic
`(generation + agent_index) % 5` rotation (`stages/generation.py::_guidance_mode_for`), so every
generation exercises all 5 modes across agents and a fixed agent rotates through all 5 modes
across generations.

### Untrusted code execution — three isolation layers, don't conflate them

LLM-generated factor Python is untrusted. There are three distinct places it runs, and the
project is very deliberate about **fail-closed, never fall back to in-process exec on the live
path**:

1. **`cogalpha/execution.py`** — the actual restricted-namespace executor
   (`compile_alpha_function`/`execute_alpha_function`). Allowlisted imports only
   (`math, numpy, pandas, scipy, scipy.stats, talib`), a minimal safe-builtins dict (no
   `eval`/`exec`/`open`/`__import__`), dispatches per-ticker via pandas' C-level
   `groupby(...).apply` (no Python ticker loop). This is the function both the in-process path
   and the subprocess-pool workers ultimately call.
2. **`cogalpha/guards/`** — static (`alpha_code.py`, pure AST walk, no execution: forbidden
   imports/calls, nested/unbounded loops, recursion, unknown columns, temporal-leakage
   patterns) and runtime (`alpha_runtime.py`, executes via #1 and checks NaN/inf/shape) guards.
   `guards/pipeline.py::DeterministicGuardPipeline` composes both for the pre-Wave-3 / MVP path.
3. **`cogalpha/execution_pool.py::AlphaExecPool`** — the **live-run** isolation substrate.
   Each candidate runs in its own `multiprocessing.get_context("spawn")` subprocess; parent does
   `proc.join(timeout)` then `proc.kill()` (SIGKILL) if still alive — this is deliberate: a
   `future.result(timeout=)` does NOT kill a CPU-bound `while True`, only an explicit kill does.
   The trusted OHLCV panel is written once into `multiprocessing.shared_memory` (not repickled
   per task). On the live engine path (`require_pool=True`, wired by
   `orchestrator.make_stage_bundle`), **all three untrusted-exec call sites** (fitness
   evaluation, quality stage-5 execution guard, leakage executed-sentinel test) are asserted to
   route through the *same* injected pool; an absent/dead pool fails an execution as
   `ok=False`, it never silently falls back to running the code in-process. If you're adding a
   new call site that executes candidate code, it needs to go through `AlphaExecPool` too, or
   you've reopened the exact hole this design closed.

### Combination + backtest (`combination.py`, `backtest.py`)

`train_combination_signal` — **not** an ensemble/average of Ridge and LightGBM. The paper
(App B.4) reports them as two independent methods; the trainer picks exactly one via
`CombinationConfig.method` (default `"lightgbm"`, matching the paper's headline numbers).
Rolling-126-day walk-forward: fit on a window strictly ending `label_horizon` (=10) days before
the predict block starts (a hard, asserted no-look-ahead embargo — raises `ValueError` if
violated), predict the next 126-day block, tile forward with no gap/overlap.
`rolling_step=126`/`label_horizon=10` are paper-pinned config fields (same
`extra="forbid"` + validator pattern as `ProtocolConfig`) — not free knobs.

`backtest.py::run_portfolio_backtest` — top-k(=50)/dropout(=5) equal-weight portfolio,
execution at the **next-period open** (`spec.execution.deal_price`), costs `open_cost=0.0005 /
close_cost=0.0015 / min_cost=¥5` from the `BenchmarkSpec.cost_model`. `topk`/`n_drop` are
sourced **only** from the spec (never a hardcoded universe-size cap — this is a deliberate
"full-universe-capable, no anti-cheat literal ceiling" invariant). Information ratio uses
`mean(excess)/std(excess) × sqrt(252)` — the Qlib annualization convention; comments explicitly
warn against "fixing" this to a per-window `sqrt(N)` normalization, it would diverge from Qlib.
All returns/IR are **excess over the benchmark** where a benchmark series is supplied.

### Checkpointing / resume (`cogalpha/artifacts.py`)

`DurableCheckpointWriter` writes `outputs/checkpoints/<run>/gen-<g>.json` via
temp-file-then-`os.replace` (atomic — a crash mid-write can never corrupt the latest
checkpoint) plus an explicit directory-fd `fsync` (the rename itself needs the *containing
directory* fsynced to be durable on POSIX, not just the file). Each checkpoint embeds a
`build_run_identity(protocol)` sha256 over the protocol's structural fields + benchmark preset
id; `--resume` refuses to continue from a checkpoint whose identity doesn't match the *current*
`ProtocolConfig`/benchmark (protects against silently resuming under a different config).
**A lone `gen-0.json` checkpoint cannot be resumed** — `run()` treats `start_generation=0` as
"fresh run", so resuming from generation 0 would re-execute (and re-pay for) generation 0;
`scripts/run.py` raises an explicit error telling you to re-run from scratch instead.

### Configuration precedence

- **Env vars (`COGALPHA_LLM_*`) are the actual live LLM config**, read by
  `cogalpha/llm/client.py::OpenAICompatibleClient.from_env()`:
  `COGALPHA_LLM_API_KEY` (falls back to `DEEPSEEK_API_KEY`/`OPENAI_API_KEY`),
  `COGALPHA_LLM_BASE_URL` (falls back to `DEEPSEEK_BASE_URL`/`OPENAI_BASE_URL`, default
  `https://api.openai.com/v1`), `COGALPHA_LLM_MODEL` (falls back to `DEEPSEEK_MODEL`/`CHAT_MODEL`),
  `COGALPHA_LLM_MAX_TOKENS`, `COGALPHA_LLM_REASONING_EFFORT`, `COGALPHA_LLM_THINKING`,
  `COGALPHA_LLM_RESPONSE_FORMAT` (default `json_object`; set to `none`/`off`/`false` to disable
  forcing JSON mode). `scripts/run.py --provider deepseek` (the default) pre-seeds
  `COGALPHA_LLM_BASE_URL=https://api.deepseek.com`, model `deepseek-v4-flash`,
  `reasoning_effort=max`, `thinking=enabled` via `os.environ.setdefault` (won't override
  something you've already exported); `--model`/`--base-url`/`--reasoning-effort`/`--thinking`/
  `--max-tokens` CLI flags force-override regardless.
- **`configs/baseline.yaml` and `configs/mvp.yaml` are DEAD — never read by any code.** Grepped
  the whole repo: no `yaml.safe_load`/`configs/` reference outside a docstring. They exist only
  as a human-readable mirror of `cogalpha/config.py`'s `BaselineExperimentConfig`/
  `MVPLoopConfig` pydantic defaults (the numeric values match exactly). If you change a default
  in `config.py`, these YAML files will silently go stale — don't assume editing them does
  anything.
- **`ProtocolConfig` (see above) is the actual source of truth for search/agent-system scale**
  — not a YAML file at all, a pydantic model with two named constructors.
- **`BenchmarkSpec` presets (`benchmark/presets.py`)** are the source of truth for the
  data/evaluation contract (universe, split windows, label, execution, portfolio rule, cost
  model) — `ProtocolConfig.benchmark_spec` embeds a resolved snapshot *by reference*, never
  copies its fields, so `BenchmarkSpec` stays the single place those numbers live.
- **`KEY.md`** (gitignored) is an optional local key file loaded by `load_key_file()` — parses
  `NAME: value` / `NAME=value` / `export NAME=value` lines, maps common aliases (`key`,
  `api_key`, `model`, `base_url`, ...) onto the canonical `COGALPHA_LLM_*` names via
  `os.environ.setdefault` (never overrides an already-exported env var).

### Evidence / artifact writing — what's redacted vs. not

Three JSONL streams per run (`cogalpha/artifacts.py::ArtifactWriter`, one lock each, so
concurrent writers never interleave half-lines): `trace`, `skill_invocations`, `model_io`. The
**redacted** model I/O recorder (`ModelIORecorder`) writes prompt/output text through
`cogalpha.redaction._redact_secret_values` (secret-**name**-pattern-based, `secrets.py`:
anything matching `(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)` in the env-var *name*, value
≥8 chars, is treated as a credential and scrubbed — not a fixed 3-name allowlist) before
persisting, and stores only a `request_sha256`/`prompt_sha256`/`output_sha256` fingerprint in
the JSONL evidence line (full text goes to separate per-call `.txt` files under
`<run>/model_io/`). `run-summary.json` (`io/run.py::summarize_cogalpha_run`) is deliberately
narrow — only run status/pool sizes, per-generation fitness aggregates (mean/min/max, never raw
per-candidate vectors), combined-signal shape (length/date-span/ticker-count, never the actual
float series), and backtest scalar metrics. **No env var value, API key, or key-file line is
ever placed in any artifact** — this is asserted by construction (every summary field is a
plain JSON primitive), not by a redaction pass applied after the fact.

## Directory structure

```
cogalpha/
├── orchestrator.py       # §2 loop (run/finalize) — the entry point to understand the whole system
├── protocol.py           # ProtocolConfig — paper_default() / validation() scale profiles
├── state.py              # CogAlphaState (5 id-ref pools + central store), semantic_factor_hash
├── config.py             # BaselineExperimentConfig / FitnessGateConfig / SplitConfig (dataclass mirror of configs/*.yaml, which are dead)
├── schemas.py             # AlphaFunction/AlphaCandidate/FitnessMetrics/GuardReport/... + legacy CogAlphaState (superseded, don't use)
├── alpha_contract.py      # OHLCV columns, allowed imports, forbidden calls/patterns — the security allowlist source of truth
├── execution.py           # restricted-namespace executor (compile_alpha_function/execute_alpha_function)
├── execution_pool.py      # AlphaExecPool — subprocess isolation, SIGKILL timeout, shared-memory panel
├── evaluation.py          # PanelBackedMetricsProvider (fitness metrics provider) + on-disk evaluation cache
├── fitness.py             # compute_predictive_metrics, apply_fitness_gate (5-metric percentile+minima gate)
├── combination.py         # rolling-126 Ridge/LightGBM combination trainer (App B.4), no-look-ahead embargo
├── backtest.py            # top-k/dropout portfolio construction, trade/cost ledger, return series, metrics
├── data.py                # OHLCV panel loading/normalization, forward-return label construction, split building
├── artifacts.py            # ArtifactWriter (trace/skill_invocations/model_io), DurableCheckpointWriter, run identity
├── instrumentation.py      # InvocationRecorder / RecordingInvoker (wraps the LLM invoker with evidence recording)
├── redaction.py / secrets.py  # secret-name policy + redaction helpers shared by artifacts/evidence writers
├── tracing.py              # CogAlphaTraceEvent (the one canonical trace-event model)
├── manifest.py             # (source manifest / provenance helpers — data_sources side)
├── benchmark/
│   ├── specs.py            # BenchmarkSpec + all its sub-contracts (typed, extra="forbid", with ProvenancedValue confidence tagging)
│   └── presets.py           # COGALPHA_CSI300_OHLCV_V1 / QUANTAALPHA_CSI300_OHLCV_V1 (comparison-only) presets
├── data_sources/           # Direct-Qlib / HF-Qlib source probes and manifests
├── guards/
│   ├── alpha_code.py        # static AST guard (imports/calls/loops/recursion/leakage patterns)
│   ├── alpha_runtime.py      # runtime numeric-stability guard (executes + checks NaN/inf/shape)
│   └── pipeline.py           # DeterministicGuardPipeline (static→runtime composition, MVP/pre-Wave-3 path)
├── skill_runtime/
│   ├── registry.py           # DOMAIN_AGENT_SPECS (21 agents) + QUALITY_SKILLS + EVOLUTION_SKILLS
│   ├── loader.py             # StandardSkillLoader — SKILL.md discovery, {base_contract} inheritance, prompt assembly
│   ├── nodes.py              # SkillNodeRuntime — typed candidate_batch/quality_decision/alpha_candidate invoke wrappers
│   ├── invocation.py         # SkillInvoker (loader + LLM client → structured artifact)
│   ├── io.py                 # prompt template value building, model-output parsing
│   └── verdict.py             # QualityVerdict schema plumbing
├── stages/                   # the Wave-3 §2-loop stages, each a DI-constructed callable over CogAlphaState
│   ├── generation.py          # §3.1+3.2 — 21-agent fan-out, Diversified Guidance rotation
│   ├── quality.py              # §3.3 — the A.3 six-step sequence (see Architecture above)
│   ├── leakage.py               # temporal-leakage stage (static scan + executed sentinel test)
│   ├── fitness.py                # §3.4 — partition stage, returns None (writes pools in place)
│   ├── adaptive.py                # §3.5 — builds {effective_CoT}/{ineffective_CoT} feedback every generation
│   ├── evolution.py                # §3.6 — mutation/crossover/crossover→mutation, exactly children_pool children
│   ├── injection.py                # §4.1 — task-agent fan-out, fires every 2 generations
│   └── defaults.py                  # _FakeInvoker / _DeterministicMetricsProvider for the offline dry-run
├── verification/trace_verifier.py  # trace-event consistency checker
├── llm/client.py            # OpenAICompatibleClient — the actual HTTP client (urllib, no SDK dependency)
├── concurrency.py            # ordered_map/resolve_max_workers — deterministic bounded-parallelism helper
└── io/run.py                  # resolve_run_dir, write_json, configure_llm_provider, load_key_file, summarize_cogalpha_run

skills/                        # 27 SKILL.md files: 21 domain agents + 4 quality checkers + 2 evolution operators
├── _base/SKILL.md             # shared App C.1 base template (not a runnable skill itself, excluded from discovery)
├── manifest.md                 # flat list of all skill names by category
└── references/                 # shared design-rationale docs linked from individual skills

scripts/
├── run.py                     # main CLI: dry-run / --real / --resume
├── prepare_direct_qlib_csi300.py  # Qlib provider_uri → merged OHLCV panel + manifest
├── prepare_hf_qlib_csi300.py       # Hugging Face QuantaAlpha/qlib_csi300 HDF5 → OHLCV panel
└── prepare_mini_csi300.py           # small synthetic dataset for fast local iteration

configs/
├── baseline.yaml               # DEAD — documentation mirror of config.py:BaselineExperimentConfig defaults, never loaded
└── mvp.yaml                     # DEAD — documentation mirror of config.py:MVPLoopConfig defaults, never loaded

docs/system-walkthrough.md     # a real (de-identified) run's full artifact trail — read this to see what a run actually produces
```

## Known gotchas

- **`configs/*.yaml` are decorative.** No code path loads them; if you want to change a default,
  edit `cogalpha/config.py`'s pydantic models directly.
- **Two `CogAlphaState` classes exist** (`cogalpha/schemas.py` and `cogalpha/state.py`). Only
  `cogalpha.state.CogAlphaState` is live; the `schemas.py` one is explicitly marked legacy/
  superseded in its own docstring.
- **`elite_pool == 0` after a completed run is a normal, honest outcome** at small/validation
  scale, not necessarily evidence of a bug — five simultaneous percentile+minima thresholds are
  a real bar. Check `run-summary.json`'s `final_pool_sizes` before assuming something broke.
- **No `tests/` directory ships in this release branch** despite `pyproject.toml` configuring
  `pytest` for one. Don't spend time hunting for a test suite here; this is a documented
  "runtime release" scoping choice (see README "仓库内容与本地内容").
- **`data/` is genuinely empty** (`.gitkeep` only) until you run one of the `scripts/prepare_*`
  scripts. There's no bundled sample dataset to poke at immediately after cloning — the fastest
  path to seeing real data flow through the system is `scripts/prepare_mini_csi300.py` or the
  dry-run (which needs no data at all, since its metrics provider is a deterministic fake).
- **`--real` is a paid LLM run.** Cap spend with `--max-invocations`; a
  `budget_exhausted`/`interrupted`/`partial` run deliberately skips the `finalize` (combination
  + backtest) step even if `elite_pool` is non-empty, specifically so it never executes
  untrusted factor code (which costs additional pool time) beyond the invocation budget you set.
- **Untrusted code must go through `AlphaExecPool` on any live path.** If you're adding a new
  call site that executes LLM-generated factor code, check whether `require_pool=True` is
  threaded through — an absent pool should fail that call closed (`ok=False`), never silently
  execute in-process.
- **`ProtocolConfig`'s pinned structural fields (children_pool=3×parent_pool, injection_every=2,
  elite_carry=2, the 65/80 percentiles, nan_reject_threshold=0.30) are not meant to be tuned**,
  even for local experimentation — they raise `ValueError` on any attempted override in both
  `paper_default()` and `validation()`. If a task seems to require changing one of these, that's
  worth surfacing to the user rather than working around silently.

## 论文复现现状：已知未实施的差异点（2026-07-09 对照，供下次会话直接续接）

论文原文摘要留档见 `docs/paper-summary.md`（不要把论文原文塞回这个文件——那份文件只记录"论文说了什么"，
这里只记录"论文说了、代码目前还没做"的部分，两者分开维护）。以下4点是逐项对照后确认的真实gap，不是笔误：

1. **多市场/多horizon泛化实验未实现**：论文§4.6 在 CSI300/CSI500/S&P500/HSI/HSCI 五个市场、10日和30日两种
   horizon下都做了实验；`benchmark/presets.py` 目前只有 `cogalpha_csi300_ohlcv_v1` 一个 preset
   （`quantaalpha_csi300_ohlcv_v1` 是对比基准，不是复现目标）。`protocol.py` 里为 S&P500 预留了MI阈值常量
   （`_SP500_MI_MINIMUM=0.012`/`SP500_PRESET_ID`），但对应的 `BenchmarkSpec` 从未被定义——是搭好架子、
   还没接上的半成品。30日horizon在代码里完全没有踪迹（`horizon_days`/combination的`label_horizon`全部
   硬编码/默认为10）。详见 `docs/paper-summary.md` "论文实验覆盖、但本仓库未实现的多市场/多horizon部分"。
2. **plateau早停机制未实现**：论文描述了基于收敛的早停规则（elite池提升 δ≤0.001、持续plateau_win代仍未
   突破则提前终止演化）；代码库里搜不到任何`plateau`/`early stop`相关逻辑，`orchestrator.py::run()`的终止
   条件被明确设计为"固定代数（`g == protocol.generations - 1`），从不因收敛/空池提前停止"——这是有意为之的
   设计选择，不是遗漏。如果以后要加早停，这是唯一需要改的地方，且要想清楚是否会破坏"固定代数、结果可复现"
   这个不变量。
3. **LLM后端与温度策略不同**：论文默认用 `gpt-oss-120b`，task agent温度区间0.7–1.2、QA agent固定0.8，
   max_tokens=4096；代码用可配置的OpenAI兼容后端（`llm/client.py::OpenAICompatibleClient`，默认
   `deepseek-v4-flash`），全局统一一个 `temperature=0.8`（`thinking=enabled`时甚至完全不传temperature），
   没有按agent角色区分温度，`max_tokens`默认不设上限（除非环境变量指定）。这是预期内的工程选择（换了可
   负担的LLM供应商），但会实质影响生成的多样性——如果发现生成结果同质化，这是一个值得排查的方向。
4. **消融实验没有运行时开关**：论文Table 3的 Baseline→+Evolution→+Adaptive→+Guidance→+Hierarchy 消融序列
   是论文用来论证架构设计的实验手段；代码里没有对应的"关闭evolution/adaptive/guidance/hierarchy中某一项"
   的配置开关（`ProtocolConfig`的结构字段是pinned的，不能通过配置绕过某个stage）。如果要做类似消融实验，
   目前只能手动改 `orchestrator.py::make_stage_bundle` 跳过某个stage，没有现成的一键开关。

**目前最重要的一点**：论文Table 1报的CogAlpha自身headline结果（IC 0.0591, RankIC 0.0814, AER 0.1639,
IR 1.8999等）——这份代码仓库自己承认还没有真实跑出过。`docs/system-walkthrough.md` 记录的唯一一次真实
LLM+真实数据validation-scale运行（216个候选）最终 `elite_pool=0`，没有回测。也就是说：**核心算法结构和
超参数与论文高度吻合，但论文的实验结论这份代码还没有真实复现过**——这不算上面4点里的"未实施"（fitness/回测
链路本身是完整实现的），而是"实施了但还没跑出过论文声称的效果"，性质不同，不要混为一谈。
