"""Engine-path run I/O for the v4.0 CogAlpha engine (BACK-04).

This package consolidates the run manifest / summary / report writers out of the
deleted ``cogalpha/runners/common.py`` island (G2 — no dead island). The single
module ``cogalpha.io.run`` owns the path-traversal-safe run-dir resolver, the
stable sorted-JSON writer, the LLM-provider/key-file env helpers, and the
rewritten ``summarize_cogalpha_run`` that aggregates the live run outcome
(``state.CogAlphaState`` + ``orchestrator.RunResult``), the per-generation
fitness evidence, the 21-02 combined signal, and the 21-01 backtest AER/IR/IC
family.
"""
