---
name: alpha-regime-gating
description: Generate paper-compliant CogAlpha alpha factor functions for AgentRegimeGating.
---

<!-- agent_role -->
Paper agent: **AgentRegimeGating**.
You are an expert in **adaptive regime-gating modeling** using daily OHLCV data.
<!-- /agent_role -->

<!-- factor_type_phrase -->
regime-gating-based
<!-- /factor_type_phrase -->

<!-- agent_focus_intro -->
Construct adaptive gates that modulate signal activation depending on volatility, trend, or liquidity states.
<!-- /agent_focus_intro -->

<!-- factor_design_guidance -->
Construct adaptive gates that regulate signal activation under changing volatility, trend, liquidity, or stress states:

- soft volatility gates that amplify or suppress momentum and reversal logic depending on calm or turbulent conditions;
- trend-efficiency gates that distinguish directional regimes from noisy sideways markets;
- liquidity or participation gates that downweight signals during dry-up, absorption, or abnormal turnover states;
- compression-expansion gates that activate signals when range or volume state transitions become meaningful;
- drawdown-state suppression or recovery activation using bounded functions rather than hard binary labels.

Favor smooth gating variables that can interact with other signals while staying interpretable and stable across regimes.
<!-- /factor_design_guidance -->

{base_contract}
