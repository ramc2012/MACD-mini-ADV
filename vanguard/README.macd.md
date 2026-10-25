# Runtime copied from Nomad Vanguard

This directory contains Claude's copied source. The runtime subset is listed in `runtime-manifest.txt`.
MACD's scheduled entry points are only `shadow_lanes.service` and the platform `lane-feed` profile.
Do not run legacy publication, funding, paper-engine, training or backfill CLIs as part of this deployment.
The service evaluates existing model artifacts and writes new non-actionable observations to its own journal.
Database fallback credentials were removed. Schema migrations are managed explicitly at cutover, not by workers.

Read `../docs/lane-port/README.md` for policy provenance, limitations, validation, activation and rollback.
