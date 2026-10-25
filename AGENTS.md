# Agent handoff: MACD-mini-ADV

Paper-trading app on Fyers (docker stack `macd-mini-parallel`: UI :3200, API :8201). Engine in engine/ (Python,
tests: run pytest inside the engine container), frontend in frontend/ (React/TS).

Its market data is being re-plumbed through the shared platform at
`/Users/ramachandran/CLAUDE PROJECTS/market-intelligence` — read that repo's AGENTS.md first; its rules apply here too
(paper only, never enter or print secrets, ask before outward-facing actions/downloads/deletes, no macOS settings).

Earlier agent notes (Claude Code memory, one fact per file — lane research, archive, live features, quirks):
/Users/ramachandran/.claude/projects/-Users-ramachandran-MACD-mini-ADV/memory/MEMORY.md

App-specific:
- runtime/settings.json holds the Fyers token: edit with a script, never print it.
- Fyers refresh: POST /api/auth/fyers/refresh (refresh token + PIN, saved via the platform's Brokers page).
- research/ folders are the owner's studies (uncommitted by choice); results in the memory notes above.
- Judge option strategies by side selection and payoff asymmetry, and say "not proven" rather than "rejected" on
  short samples (memory: judging-option-strategies.md).
