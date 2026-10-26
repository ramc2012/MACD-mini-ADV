"""Read shared market features with provenance; immutable observations for later training.

No broker call, historical publication, execution API or outcome field is exposed.
"""
from datetime import date,datetime,time,timedelta
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from . import source

# Group, table, entity key, event key, receipt key, cadence, model inputs.
SPECS=(
 ('flow','features_flow','symbol','ts',None,'bar',('ivs','ivs_z','skew','skew_z','os_pctile','oi_state','pcr_z','flow_score','n_ingredients','ce_state','pe_state')),
 ('regime','regime','symbol','ts',None,'bar',('net_gex','regime','gamma_flip_level','gex_percentile')),
 ('timing','timing','symbol','ts',None,'bar',('timing_state','timing_score','rvol','va_position')),
 ('sector','sector_rs','sector20','ts',None,'intraday_day',('rs_z5','rs_z20','rs_z60')),
 ('leadlag','leadlag','symbol','dt',None,'intraday_day',('best_lag','corr')),
 ('surface','iv_surface','symbol','dt','computed_at','intraday_day',('atm_iv','ivs','skew_25d','iv_percentile','iv_rank','d_atm_iv','n_strikes','n_good','delta_span')),
 ('chain','option_premium_ratios','symbol','ts','computed_at','bar',('straddle_to_spot','normalized_straddle','atm_put_call_premium_ratio','atm_call_put_extrinsic_ratio','call_itm_atm_extrinsic_ratio','call_otm_atm_extrinsic_ratio','put_itm_atm_extrinsic_ratio','put_otm_atm_extrinsic_ratio','call_wing_iv_ratio','put_wing_iv_ratio','strangle_straddle_ratio','premium_pcr','wing_valid','n_strikes','n_contracts')),
 ('futures','futures_oi_baselines','symbol','ts','computed_at','intraday_day',('d_price_pct','d_oi_pct','d_oi_pct_z','oi_z','volume_z','oi_pctile','oi_state','activity_surge','is_rollover','lookback_sessions')),
 ('profile','features_mp','underlying','dt','computed_at','eod',('poc','vah','val','va_width_pct','ib_width_pct','ib_pct_rank','range_over_ib','close_pos','day_type','poor_high','poor_low','tail_high_pct','tail_low_pct','value_shift','poc_migration_pct','va_overlap','w_loc','m_loc','of_available','of_delta_share','of_close_vs_vwap','of_rvol20','sig_strong_close','sig_oversold_mtf','exp_range_pct')),
 ('positioning','oi_positioning','symbol','dt','computed_at','eod',('d_oi_pct','mwpl_pct','oi_pcr','d_oi_pcr','d_price_pct','ret_5d','ret_20d','ret_60d','oi_state','oi_state_strength')),
 ('delivery','bhavcopy_delivery','symbol','dt','synced_at','eod',('delivery_pct','volume','value','deliverable_qty')),
 ('mwpl','fo_mwpl_snapshot','symbol','snapshot_date','synced_at','eod',('utilisation_pct','open_interest','market_wide_position_limit')),
 ('fx','usdinr_daily',None,'dt','synced_at','eod',('close','volume')),
 ('market','market_sentiment',None,'dt','computed_at','intraday_day',('fii_fut_index_net','fii_fut_stock_net','fii_opt_index_net','fii_index_long_ratio','dii_fut_index_net','client_fut_index_net','client_opt_index_net','pro_fut_index_net','d_fii_fut_index_net','market_oi_pcr','market_volume_pcr','d_market_oi_pcr','index_atm_iv','d_index_atm_iv','median_stock_iv','iv_percentile','advance_decline_ratio','pct_above_20d','median_ret_1d','net_oi_bias','sentiment_score')),
)


def stamp(value):
    if isinstance(value,datetime):
        if value.tzinfo is None:raise ValueError('Timezone required')
        return value.astimezone(source.IST)
    return datetime.combine(value,time(15,30),source.IST)


def block(group,row,event,receipt,cadence,fields,as_of):
    if not row:return {group+'.status':'missing'}
    observed=stamp(row[event])
    if cadence=='bar':observed+=timedelta(minutes=30)
    if cadence=='intraday_day' and not isinstance(row[event],datetime):observed=datetime.combine(row[event],time(9,15),source.IST)
    received=stamp(row[receipt]) if receipt and row.get(receipt) else as_of
    if observed>as_of or received>as_of:return {group+'.status':'not_available_at_decision'}
    max_age=timedelta(days=5) if cadence=='eod' else timedelta(days=4) if cadence=='intraday_day' else timedelta(hours=20)
    # Overnight/weekend values retain their age; never advertise them as a fresh intraday observation.
    age=(as_of-observed).total_seconds()
    result={group+'.status':'stale' if as_of-observed>max_age else 'prior_session' if observed.date()<as_of.date() else 'observed',group+'.event_at':observed.isoformat(),
            group+'.available_at':received.isoformat(),group+'.age_hours':round(age/3600,2)}
    if not receipt:result[group+'.receipt_basis']='first_observed_now; historical availability unverified'
    for key in fields:
        value=row.get(key)
        if isinstance(value,Decimal):value=float(value)
        if value is None or isinstance(value,(str,bool,int)) or isinstance(value,float) and math.isfinite(value):result[group+'.'+key]=value
    return result


def normalize(value):
    value=str(value or '').removeprefix('NSE:').removeprefix('BSE:').removesuffix('-INDEX').removesuffix('-EQ')
    return {'NIFTY50':'NIFTY','NIFTYBANK':'BANKNIFTY'}.get(value,value)


def context_database():
    path=Path(os.environ.get('FEATURE_CONTEXT_DATABASE','/runtime/shadow_lanes/context.sqlite3'));path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,instrument TEXT NOT NULL,phase TEXT NOT NULL,payload TEXT NOT NULL)')
    c.execute('CREATE INDEX IF NOT EXISTS context_instrument_time ON snapshots(instrument,observed_at)')
    return c


def build(connection,candidates,as_of,phase='next_session_watchlist',persist=True):
    if as_of.tzinfo is None or len(candidates)>10:raise ValueError('Bounded, timezone-stamped request required')
    with connection.cursor(cursor_factory=RealDictCursor) as q:
        instruments=[c['instrument'] for c in candidates]
        q.execute('SELECT instrument_key,underlying,expiry,strike,option_type FROM fo_contract_catalog WHERE instrument_key=ANY(%s)',(instruments,))
        contracts={r['instrument_key']:dict(r) for r in q.fetchall()}
        q.execute('SELECT symbol,spot_instrument_key,sector20,sector_group FROM fo_underlying_catalog LEFT JOIN sector_taxonomy USING(symbol) WHERE spot_instrument_key=ANY(%s) OR symbol=ANY(%s)',
                  (instruments,[normalize(c.get('facts',{}).get('underlying') or c.get('symbol')) for c in candidates]))
        catalog=[dict(r) for r in q.fetchall()]
        resolved={}
        for c in candidates:
            meta=contracts.get(c['instrument'],{})
            supplied=normalize(c.get('facts',{}).get('underlying') or c.get('symbol') or meta.get('underlying'))
            entry=next((r for r in catalog if r['spot_instrument_key']==c['instrument'] or r['symbol']==supplied),None)
            # Contract identities already resolve to the authoritative catalog, without a symbol guess.
            if meta and not entry:
                q.execute('SELECT s.symbol,s.sector20,s.sector_group FROM sector_taxonomy s WHERE symbol=%s',(meta['underlying'],));entry=q.fetchone()
            resolved[c['id']]={'symbol':meta.get('underlying') or (entry or {}).get('symbol'),'sector':(entry or {}).get('sector20'),
                               'sector_group':(entry or {}).get('sector_group'),
                               'expiry':meta.get('expiry') or c.get('facts',{}).get('expiry'),'strike':meta.get('strike') or c.get('facts',{}).get('strike'),
                               'side':meta.get('option_type') or c.get('side')}
        symbols=list({r['symbol'] for r in resolved.values() if r['symbol']});sectors=list({r['sector'] for r in resolved.values() if r['sector']})
        result={c['id']:{'context_version':'market-context-v2','phase':phase,'context_as_of':as_of.isoformat(),'underlying':resolved[c['id']]['symbol']} for c in candidates}
        for group,table,key,event,receipt,cadence,fields in SPECS:
            universe=sectors if key=='sector20' else symbols
            cutoff=as_of-timedelta(minutes=30) if cadence=='bar' else as_of
            query=sql.SQL('SELECT ')+(sql.SQL('DISTINCT ON ({}) * FROM {}').format(sql.Identifier(key),sql.Identifier(table)) if key else sql.SQL('* FROM {}').format(sql.Identifier(table)))
            query+=sql.SQL(' WHERE {}<=%s').format(sql.Identifier(event));params=[cutoff]
            if key:query+=sql.SQL(' AND {}=ANY(%s)').format(sql.Identifier(key));params.append(universe)
            if receipt:query+=sql.SQL(' AND {}<=%s').format(sql.Identifier(receipt));params.append(as_of)
            # EOD fields from the current day are unavailable until the completed session.
            if cadence=='eod':query+=sql.SQL(' AND {}<=%s').format(sql.Identifier(event));params.append(as_of.date() if as_of.time()>=time(15,30) else source.previous_session(as_of.date()))
            query+=sql.SQL(' ORDER BY ')+(sql.SQL('{},').format(sql.Identifier(key)) if key else sql.SQL(''))+sql.SQL('{} DESC').format(sql.Identifier(event))
            if receipt:query+=sql.SQL(',{} DESC').format(sql.Identifier(receipt))
            if not key:query+=sql.SQL(' LIMIT 1')
            q.execute(query,params);rows=[dict(r) for r in q.fetchall()];by={r[key]:r for r in rows} if key else {}
            for c in candidates:
                meta=resolved[c['id']];entity=meta['sector'] if key=='sector20' else meta['symbol'];row=by.get(entity) if key else (rows[0] if rows else None)
                result[c['id']].update(block(group,row,event,receipt,cadence,fields,as_of))
        for c in candidates:
            meta=resolved[c['id']]
            if meta['symbol'] and meta['expiry'] and meta['strike'] and meta['side'] in ('CE','PE'):
                q.execute('''SELECT * FROM option_iv WHERE symbol=%s AND expiry=%s AND strike=%s AND option_type=%s
                   AND dt<=%s AND computed_at<=%s ORDER BY dt DESC,computed_at DESC LIMIT 1''',
                   (meta['symbol'],meta['expiry'],meta['strike'],meta['side'],as_of.date(),as_of));row=q.fetchone()
                result[c['id']].update(block('greeks',dict(row) if row else None,'dt','computed_at','intraday_day',
                      ('iv','iv_uncertainty','delta','gamma','theta','vega','days_to_expiry','log_moneyness','quality','quality_flags'),as_of))
            else:result[c['id']]['greeks.status']='exact_contract_metadata_unavailable'
            if meta['symbol']:
                from .futures_context import supplement
                result[c['id']].update(supplement(connection,meta['symbol'],as_of))
                q.execute('SELECT min(results_date) AS next_results FROM results_calendar WHERE symbol=%s AND results_date>=%s AND synced_at<=%s',(meta['symbol'],as_of.date(),as_of));r=q.fetchone()
                result[c['id']]['events.days_to_results']=(r['next_results']-as_of.date()).days if r and r['next_results'] else None
                q.execute('SELECT count(*) AS count,max(dt) AS latest FROM announcements WHERE symbol=%s AND dt>=%s AND dt<=%s AND synced_at<=%s',(meta['symbol'],as_of-timedelta(days=3),as_of,as_of));r=q.fetchone()
                result[c['id']]['events.announcements_3d']=r['count']
                q.execute('''SELECT count(*) AS count,
                    coalesce(sum(quantity*price) FILTER(WHERE upper(deal_type) IN ('BUY','B')),0) AS buy_value,
                    coalesce(sum(quantity*price) FILTER(WHERE upper(deal_type) IN ('SELL','S')),0) AS sell_value,
                    max(synced_at) AS available_at FROM bulk_block
                    WHERE symbol=%s AND dt>=%s AND dt<=%s AND synced_at<=%s''',
                    (meta['symbol'],as_of.date()-timedelta(days=3),as_of.date(),as_of));deals=q.fetchone()
                result[c['id']].update({'deals.disclosed_trades_3d':deals['count'],
                    'deals.disclosed_buy_value_3d':float(deals['buy_value']),
                    'deals.disclosed_sell_value_3d':float(deals['sell_value']),
                    'deals.status':'observed_disclosures' if deals['count'] else 'no_matching_disclosures',
                    'deals.available_at':deals['available_at'].isoformat() if deals['available_at'] else None})
                entry_day=source.next_session(as_of.date()) if phase=='next_session_watchlist' else as_of.date()
                q.execute('SELECT count(*) AS n FROM fo_security_ban WHERE symbol=%s AND snapshot_date=%s AND synced_at<=%s',(meta['symbol'],entry_day,as_of));r=q.fetchone()
                banned=bool(r['n'])
                q.execute('SELECT max(snapshot_date) AS day FROM fo_security_ban WHERE snapshot_date<=%s AND synced_at<=%s',(entry_day,as_of));received=q.fetchone()['day']
                result[c['id']]['risk.entry_session']=str(entry_day)
                result[c['id']]['risk.fno_banned']=banned if received==entry_day else None
                result[c['id']]['risk.fno_ban_list_status']='entry_session_published' if received==entry_day else 'missing_or_stale'
                q.execute('''SELECT DISTINCT ON(driver) * FROM cross_asset_beta WHERE sector_group=%s AND dt<%s AND computed_at<=%s ORDER BY driver,dt DESC''',(meta['sector_group'],as_of.date(),as_of))
                drivers=q.fetchall();result[c['id']]['cross_asset.status']='observed' if drivers else 'not_applicable_or_missing'
                for r in drivers:
                    if r['driver'] not in ('lme_metals','crude_oil','usdinr'):continue
                    prefix='cross_asset.'+r['driver']
                    for k in ('beta','corr','lookback_days'):result[c['id']][prefix+'.'+k]=float(r[k]) if r[k] is not None else None
                    result[c['id']][prefix+'.event_at']=str(r['dt'])
            result[c['id']]['feature_count']=sum('.' in k and not k.endswith(('.status','.event_at','.available_at','.age_hours','.receipt_basis')) for k in result[c['id']])
    if persist:
        with context_database() as db:
            for c in candidates:
                payload=json.dumps(result[c['id']],sort_keys=True,allow_nan=False,default=str)
                identity=hashlib.sha256((as_of.isoformat()+c['instrument']+phase+payload).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)',(identity,as_of.isoformat(),c['instrument'],phase,payload))
    return result


def capture_universe(as_of):
    """Capture all active names, including those never selected, with actual observation times."""
    c=source.connect(readonly=True)
    try:
        with c.cursor(cursor_factory=RealDictCursor) as q:
            q.execute("SELECT symbol,spot_instrument_key FROM fo_underlying_catalog WHERE kind='STOCK' AND fno_active IS TRUE AND spot_instrument_key IS NOT NULL ORDER BY symbol")
            rows=q.fetchall()
        for start in range(0,len(rows),10):
            candidates=[{'id':r['symbol'],'symbol':r['symbol'],'instrument':r['spot_instrument_key'],'facts':{'underlying':r['symbol']}} for r in rows[start:start+10]]
            build(c,candidates,as_of,'universe_observation')
        return len(rows)
    finally:c.close()


def summary():
    with context_database() as c:
        n=c.execute('SELECT count(*) FROM snapshots').fetchone()[0]
        latest=c.execute("SELECT max(observed_at) FROM snapshots WHERE phase='universe_observation'").fetchone()[0]
        rows=c.execute("SELECT count(DISTINCT instrument) FROM snapshots WHERE observed_at=? AND phase='universe_observation'",(latest,)).fetchone()[0]
        payloads=c.execute("SELECT payload FROM snapshots WHERE observed_at=? AND phase='universe_observation'",(latest,)).fetchall()
    groups={}
    for (payload,) in payloads:
        for key,value in json.loads(payload).items():
            if key.endswith('.status'):
                counts=groups.setdefault(key[:-7],{});counts[value]=counts.get(value,0)+1
    return {'snapshots':n,'latest_observed_at':latest,'latest_names':rows,'captured_before_selection':True,'groups':groups}
