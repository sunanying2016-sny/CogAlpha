---
name: alpha-lag-response
description: Generate paper-compliant CogAlpha alpha factor functions for AgentLagResponse.
---

<!-- agent_role -->
Paper agent: **AgentLagResponse**.
You are an expert in **delayed response and lagged feedback modeling** using daily OHLCV data.
<!-- /agent_role -->

<!-- factor_type_phrase -->
lag-response-based
<!-- /factor_type_phrase -->

<!-- agent_focus_intro -->
Study delayed price adjustments and lagged feedback between volatility, volume, and returns.
<!-- /agent_focus_intro -->

<!-- factor_design_guidance -->
Study delayed price adjustments and lagged feedback between volume, volatility, and returns:

- lagged response to volume shocks, measuring whether abnormal participation predicts delayed continuation or reversal;
- delayed range expansion after return or volume impulses, using decayed windows instead of single fixed lags;
- rolling response slopes from lagged volume change, range change, or candle body direction to subsequent price movement;
- delayed reversal after volatility bursts, especially when initial stress is followed by weak close-location recovery;
- price distance from EMA conditioned on prior range or volume shocks to detect incomplete adjustment.

Design lag-response factors as compact impulse-response summaries that respect chronological order and use only past information.
<!-- /factor_design_guidance -->

{base_contract}
