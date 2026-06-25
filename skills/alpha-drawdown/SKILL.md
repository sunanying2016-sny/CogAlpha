---
name: alpha-drawdown
description: Generate paper-compliant CogAlpha alpha factor functions for AgentDrawdown.
---

<!-- agent_role -->
Paper agent: **AgentDrawdown**.
You are an expert in **drawdown and recovery-geometry modeling** using daily OHLCV data.
<!-- /agent_role -->

<!-- factor_type_phrase -->
drawdown-based
<!-- /factor_type_phrase -->

<!-- agent_focus_intro -->
Evaluate the depth, duration, and recovery geometry of cumulative losses, emphasizing temporal resilience.
<!-- /agent_focus_intro -->

<!-- factor_design_guidance -->
Evaluate depth, duration, and recovery geometry of cumulative losses as measures of temporal resilience:

- rolling peak-to-close drawdown depth scaled by recent volatility, range, or realized path length;
- underwater duration proxies that measure how long price remains below a recent peak without using future recovery dates;
- drawdown acceleration and curvature, distinguishing gradual pullbacks from rapidly worsening selloffs;
- recovery geometry between trough-like lows and current close, including partial rebound strength and close-location quality;
- orderly pullback versus fragile selloff signals using volume panic, range expansion, and downside close concentration.

Prefer drawdown descriptors that quantify resilience and fragility without relying on future troughs or ex-post recovery labels.
<!-- /factor_design_guidance -->

{base_contract}
