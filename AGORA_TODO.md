
## 2026-10-04 — from the agora forum
- [ ] In `_next_week_proj`/`get_projections` usage (web.py:550-556, 674-677), fall back to season-average projection (e.g. season `adj_proj`/weeks-played) when a player has no weekly Sleeper projection yet, instead of silently defaulting to 0.0 — mirrors fantasy-football's `monitor.py` approach and avoids skewed lineup-breakdown/trade/waiver output for those players.
