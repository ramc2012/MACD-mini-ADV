"""MACD Vanguard/CBE shadow lanes. GETs only; all prospective writes use a new SQLite journal."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys

from fastapi import Depends, FastAPI, Header, HTTPException, Body
import psycopg2
from psycopg2.extensions import make_dsn

from . import source
from .store import ShadowStore

LANES = ("vanguard", "cbe")
# Only derived market-data modules. Legacy selection/publication/paper/funding commands are never invoked.
FEATURE_STEPS = (
    ("implied_vol", "features/m_implied_vol.py", "--lookback-days", "1", "--write"),
    ("premium_ratios", "features/m_option_ratios.py", "--lookback-days", "3", "--write"),
    ("iv_surface", "features/m_iv_surface.py", "--lookback-days", "120", "--write"),
    ("sentiment", "features/m_sentiment.py", "--lookback-days", "5", "--write"),
    ("flow", "features/m2_flow.py", "--lookback-days", "130"),
    ("regime", "features/m3_gex.py", "--lookback-days", "60"),
    ("sector", "features/m4_sector.py", "--lookback-days", "90"),
    ("timing", "features/m5_timing.py", "--lookback-days", "3", "--buffer-days", "60", "--write", "--no-spot-check"),
)


class ShadowWorker:
    def __init__(self, store, paper=None):
        self.store = store
        self.paper = paper
        self.features_bar = None
        self.feature_error = None

    def check(self, now, diagnostic=False):
        """No missed-session publication, legacy write, or order interface exists here."""
        existing = {(p["lane"], p["source_session"]) for p in self.store.publications()}
        for lane in LANES:
            day = source.source_session(now, lane)
            if not diagnostic and not source.publication_window(now, lane):
                self.store.status(lane, now.isoformat(), dict(status="awaiting_window", blockers=["Outside the prospective publication window"],
                    source_session=str(day), policy=source.POLICIES[lane], feature_error=self.feature_error))
                continue
            if (lane, str(day)) in existing:
                self.store.status(lane, now.isoformat(), dict(status="frozen", blockers=[], source_session=str(day), policy=source.POLICIES[lane]))
                continue
            connection = None
            try:
                connection = source.connect(readonly=True)
                result = getattr(source, lane)(connection, now)
                if lane == "vanguard" and self.feature_error:
                    result.update(status="blocked", blockers=[f"Market feature refresh failed: {self.feature_error}"], watchlist=[])
                if self.paper and result.get('status')=='ok' and os.environ.get('LOCAL_DECISION_MODE','off')!='filter':
                    result.update(status='blocked',blockers=['Paper cohorts require Qwen filter mode'],watchlist=[])
                if (lane=='vanguard' or self.paper) and not diagnostic and result.get('status')=='ok' and os.environ.get('LOCAL_DECISION_MODE','off')!='off':
                    from .local_model import apply_review
                    result=apply_review(result,now,lane=lane) if self.paper else apply_review(result,now)
                    # A slow review cannot freeze a missed or previous-session observation.
                    finished=datetime.now(source.IST)
                    if finished.date()!=now.astimezone(source.IST).date() or not source.publication_window(finished,lane):
                        result.update(status='blocked',blockers=['Local review completed outside the publication window'],watchlist=[])
                if self.paper:
                    result['paper_account_id']=self.paper.books[lane].account_id
                    result['policy']=result.get('policy',source.POLICIES[lane])+'-paper-candidates-v1'
                    result['watchlist']=[{**row,'actionable':False,'actionable_reason':'Paper candidate only; fresh next-session Qwen, quote and risk checks required'} for row in result.get('watchlist',[])]
                result.update(shadow_only=True, execution_enabled=False, diagnostic=diagnostic)
                self.store.status(lane, now.isoformat(), result)
                if not diagnostic and source.publication_window(now, lane) and result.get("source_session") == str(now.astimezone(source.IST).date()):
                    if result.get("status") == "ok" and result.get("watchlist"):
                        inserted=self.store.publish(lane, day, datetime.now(source.IST).isoformat() if result.get('local_review') else now.isoformat(), result.get('policy',source.POLICIES[lane]), result)
                        if inserted and self.paper:
                            self.paper.offer(next(p for p in self.store.publications(lane) if p['source_session']==str(day)))
            except Exception as exc:
                self.store.status(lane, now.isoformat(), dict(status="blocked", blockers=[f"Input check failed: {type(exc).__name__}"],
                    source_session=str(day), policy=source.POLICIES[lane]))
            finally:
                if connection is not None:
                    connection.close()
        # Outcomes are additive observations of already-frozen identities, never regenerated selections.
        connection = None
        try:
            publications = self.store.publications()
            if publications:
                connection = source.connect(readonly=True)
                for publication in publications:
                    for instrument, horizon, result in source.observe_outcomes(connection, publication, now):
                        self.store.record_outcome(publication["id"], instrument, horizon, now.isoformat(), result)
        finally:
            if connection is not None:
                connection.close()

    async def features(self, now):
        local = now.astimezone(source.IST)
        if not source.is_session(local.date()) or not (9 * 60 + 46 <= local.hour * 60 + local.minute < 15 * 60 + 40):
            return
        # Completed :15/:45 grid, with 90 seconds for the input sweep to commit.
        shifted = local - timedelta(seconds=90)
        minutes = (shifted.hour * 60 + shifted.minute - 9 * 60 - 15) // 30
        key = (str(local.date()), minutes)
        if key == self.features_bar:
            return
        env = dict(os.environ)
        env["VANGUARD_DATABASE_URL"] = make_dsn(host=env.get("MI_PG_HOST", "nomadcurie_db"),
            dbname=env.get("LANE_SOURCE_DB", "nomadcurie"), user=env["MI_PG_USER"], password=env["MI_PG_PASSWORD"])
        env["SHARED_MP_REDIS_URL"] = ""
        env["VANGUARD_SCHEMA_MANAGED"] = "true"
        self.feature_error = None
        from .context import sync_participants
        destination = source.connect(readonly=False)
        try:
            await asyncio.to_thread(sync_participants, destination)
        finally:
            destination.close()
        for name, script, *args in FEATURE_STEPS:
            process = await asyncio.create_subprocess_exec(sys.executable, str(source.VG_ROOT / script), *args,
                cwd=source.VG_ROOT, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, error = await asyncio.wait_for(process.communicate(), timeout=180)
                if process.returncode:
                    category = "permission denied" if b"permission denied" in error else "stage failed"
                    self.feature_error = f"{name}: {category} (exit {process.returncode})"
                    break
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                raise
        self.features_bar = key

    async def run(self):
        while True:
            try:
                now = datetime.now(source.IST)
                await self.features(now)
                await asyncio.to_thread(self.check, datetime.now(source.IST))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for lane in LANES:
                    self.store.status(lane, datetime.now(source.IST).isoformat(),
                                      {"status": "blocked", "blockers": [f"Worker failed: {type(exc).__name__}"]})
            await asyncio.sleep(30)


def create_app(store=None, worker_enabled=None, paper_manager=None):
    store = store or ShadowStore(os.environ.get("SHADOW_DATABASE_PATH", "/runtime/shadow_lanes/observations.sqlite3"))
    paper=paper_manager
    if paper is None and os.environ.get('PAPER_LANES_ENABLED','false')=='true':
        from paper_lanes.manager import Manager
        paper=Manager()
    worker = ShadowWorker(store,paper)
    enabled = os.environ.get("SHADOW_WORKER_ENABLED", "false").lower() == "true" if worker_enabled is None else worker_enabled

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(worker.run())] if enabled else []
        if paper:tasks.extend([asyncio.create_task(paper.run_marks(store)),asyncio.create_task(paper.run_reviews()),asyncio.create_task(paper.run_reconcile(store))])
        yield
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    api = FastAPI(title="MACD shadow lanes", lifespan=lifespan)
    api.state.store, api.state.worker = store, worker
    api.state.paper=paper

    def authorize(x_macd_token: str = Header(default="")):
        expected = os.environ.get("MACD_API_TOKEN", "")
        if expected and x_macd_token != expected:
            raise HTTPException(401, "Invalid API token")

    from .nomad_views import install
    install(api, store, authorize,paper=paper)

    @api.get("/health")
    def health():
        return {"ok": True, "shadow_only": paper is None, "execution_enabled": paper is not None,
                "execution_mode":"paper","live_orders_possible":False,"worker_enabled": enabled}

    @api.get('/api/paper-lanes/{lane}',dependencies=[Depends(authorize)])
    def paper_snapshot(lane:str):
        if lane not in LANES:raise HTTPException(404,'Unknown lane')
        if not paper:return {'enabled':False,'lane':lane}
        return {'enabled':True,**paper.snapshot(lane),'readiness':store.snapshot(lane)['readiness']}

    @api.post('/api/paper-lanes/{lane}/control',dependencies=[Depends(authorize)])
    def paper_control(lane:str,body:dict=Body(...)):
        if lane not in LANES or not paper:raise HTTPException(404,'Paper lane unavailable')
        if set(body)!={'action'} or body['action'] not in ('pause','resume'):raise HTTPException(400,'Only pause/resume new entries is supported')
        paper.books[lane].control(body['action']=='pause',datetime.now(source.IST))
        return {'ok':True,'paused':body['action']=='pause','exits_remain_managed':True}

    @api.get("/api/shadow-lanes/{lane}", dependencies=[Depends(authorize)])
    def snapshot(lane: str):
        if lane not in LANES:
            raise HTTPException(404, "Unknown shadow lane")
        return {**store.snapshot(lane), "worker_enabled": enabled, "legacy_history_preserved": True,
                'paper_enabled':paper is not None,'execution_mode':'paper',
                "publication_window": "14:45–15:15 IST" if lane == "vanguard" else "15:15–15:35 IST",
                "target": "Exact stock-option performers over 1–3 sessions, plus a BTST observation" if lane == "vanguard" else "Long-only daily stock-direction research"}

    @api.get("/api/shadow-lanes/{lane}/archive", dependencies=[Depends(authorize)])
    def archive(lane: str):
        if lane not in LANES:
            raise HTTPException(404, "Unknown shadow lane")
        out = {"lane": lane, "read_only": True, "separate_from_new_shadow": True}
        if lane == "cbe":
            path = Path(os.environ.get("LEGACY_CBE_FILE", "/legacy/cbe/paper_positions.json"))
            try:
                state = json.loads(path.read_text())
                out.update(status="archived", book=state)
            except (OSError, ValueError):
                out.update(status="unavailable", reason="The original CBE history is retained but not mounted here")
        else:
            connection = None
            try:
                connection = source.connect(readonly=True)
                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT source_session,item_count,actionable_count,status,decision_at FROM vanguard_swing_watchlist_runs ORDER BY source_session DESC LIMIT 30")
                    out.update(status="archived", runs=[dict(r) for r in cur.fetchall()])
            except Exception as exc:
                out.update(status="unavailable", reason=f"Retained history cannot be read: {type(exc).__name__}")
            finally:
                if connection is not None:
                    connection.close()
        return out

    return api


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8430)
