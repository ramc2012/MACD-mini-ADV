# Nomad desks in MACD

Deployed at **2026-10-25 22:10:00 IST**. The original Nomad frontend-v2 desks are in `frontend/src/nomad/`;
`provenance.json` records copied files and adaptations. These desks use the native MACD quote feed and the
GET-only `/api/nomad/*` sidecar proxy. Fresh prospective journals and retained Nomad paper histories remain separate.
The inherited funded engines were not restarted or enabled. Both new lanes remain shadow-only, with execution disabled.

Full implementation, verification, owner Upstox login steps and recovery limits:
`/Users/ramachandran/CLAUDE PROJECTS/market-intelligence/docs/UPSTOX_LANE_UI_20261025.md`.

Build with existing local dependencies: `cd frontend && npm ci --offline --ignore-scripts --no-audit --no-fund && npm run build`.
Generated scoped Tailwind CSS is committed; normal builds do not require the Nomad checkout or Tailwind installation.
The four dependency tarballs and license files were bundled from the original locally installed packages without
downloading new dependencies. `frontend/vendor/provenance.json` records their versions.

Database administrator provisioning of the read-only historical UI grants is explicit in
`shadow_lanes/read_history_grants.sql`; application startup never grants itself access. Nomad runtime directories
are mounted read-only. Missing restored datasets are shown unavailable, without advancing old books.
