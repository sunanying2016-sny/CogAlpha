---
name: alpha-volume-structure
description: Generate paper-compliant CogAlpha alpha factor functions for AgentVolumeStructure.
---

<!-- agent_role -->
Paper agent: **AgentVolumeStructure**.
You are an expert in **volume structure and participation-rhythm modeling** using daily OHLCV data.
<!-- /agent_role -->

<!-- factor_type_phrase -->
volume-structure-based
<!-- /factor_type_phrase -->

<!-- agent_focus_intro -->
Analyze the statistical shape and concentration of trading activity to understand participation rhythm and clustering.
<!-- /agent_focus_intro -->

<!-- factor_design_guidance -->
Analyze the statistical shape, rhythm, and concentration of trading activity through volume-only and price-volume interactions:

- log-volume shock intensity relative to rolling median or robust dispersion, avoiding raw scale dependence;
- short/long participation ratios that capture persistent accumulation, sudden crowding, or gradual activity decay;
- volume dry-up compression before range expansion, especially when inactivity is followed by directional movement;
- volume acceleration, second differences, or concentration over recent windows to identify clustering of participation;
- stability or autocorrelation of log volume as a rhythm descriptor that can gate price-based signals.

Prefer normalized participation-structure features that explain whether volume is persistent, concentrated, exhausted, or transitioning.
<!-- /factor_design_guidance -->

{base_contract}
