"""Retained pre-entry Smart snapshots plus separately labeled reconstructed context.

Raw outcome data stays outside model inputs. No historical receipt is invented.
"""
from collections import Counter,defaultdict
from datetime import timedelta,date
from pathlib import Path
import argparse,bisect,hashlib,json,math
from .build_corpus import stamp,split,message


def smart_entries(raw):
    from .policy import SMART_FEATURE_NOTES
    t=raw['tables'];closes=defaultdict(list);excluded=Counter();out=[]
    for r in t['closed_positions']:closes[r['position_id']].append(json.loads(r['payload']))
    buys={(p['symbol'],stamp(p['timestamp'])):p for p in (json.loads(r['payload']) for r in t['trades']) if p['side']=='BUY'}
    journals={r['order_id']:r for r in t['smart_journal'] if r['decision']=='TAKEN' and r['source']=='macd' and r.get('order_id')}
    for identity,rows in closes.items():
        try:
            first=rows[0];last=max(rows,key=lambda r:stamp(r['exit_time']));entry=stamp(first['entry_time'])
            trade=buys.get((first['symbol'],entry));journal=journals.get((trade or {}).get('order_id'))
            if not journal:excluded['missing_exact_macd_journal']+=1;continue
            if last.get('partial') or last.get('remaining_quantity') or sum(r['quantity'] for r in rows)!=trade['quantity']:
                excluded['incomplete_close']+=1;continue
            at=stamp(journal['at'])
            if at>entry or (entry-at).total_seconds()>30:excluded['journal_not_before_entry']+=1;continue
            detail=json.loads(journal['detail']);features=detail.get('features')
            if not features or not isinstance(features,dict):excluded['no_recorded_features']+=1;continue
            facts={k:v for k,v in features.items() if v is None or isinstance(v,(str,bool,int)) or isinstance(v,float) and math.isfinite(v)}
            facts.update(source='macd',phase='smart_entry',feature_snapshot_at=at.isoformat(),signal_price=journal['signal_price'],
                         ranker_score=journal['score'],historical_exit_policy='recorded_smart_book_unversioned')
            facts.update(SMART_FEATURE_NOTES)
            entry_value=sum(r['entry_price']*r['quantity'] for r in rows);exit_value=sum(r['exit_price']*r['quantity'] for r in rows)
            gross=sum(r['gross_pnl'] for r in rows);fees=max(sum(r.get('fees',0) for r in rows),.001*(entry_value+exit_value))
            if entry_value<=0 or not all(math.isfinite(x) for x in (gross,fees,entry_value,exit_value)):raise ValueError()
            out.append(dict(id='smart-context:'+identity,lane='smart',decision_at=at.isoformat(),label_available_at=last['exit_time'],
                 instrument=first['symbol'],side=journal['option_type'],reference_price=journal['check_price'],features=facts,
                 net_return=(gross-fees)/entry_value,stress_return=(gross-max(fees,.005*(entry_value+exit_value)))/entry_value,
                 label_kind='recorded_smart_close_and_preentry_context',source=raw['source'],quality_tier='recorded_preentry_snapshot',
                 selection_bias='Executed Smart MACD-source positions from one book; historical admission and exit-policy selection; not all rejected signals'))
        except (ValueError,TypeError,KeyError,ZeroDivisionError):excluded['invalid_row']+=1
    return out,dict(excluded)


def prior_context(row,market):
    """Prior-session context only; original historical receipt remains unverified.

    Aggregates recomputed later are not presented as available during old sessions.
    Never use a current-day daily aggregate for a pre-close decision.
    """
    at=stamp(row['decision_at']);symbol=row.get('symbol') or row['instrument'].removeprefix('NSE:').removesuffix('-EQ')
    symbol=market.get('instrument_symbols',{}).get(row['instrument'],symbol)
    facts={};receipts={}
    for spec in market['specs']:
        group,table,key,event,receipt,cadence,fields=spec
        entity=market.get('sectors',{}).get(symbol) if key=='sector20' else symbol
        matches=[r for r in market['rows'].get(table,[]) if (key is None or r.get(key)==entity) and str(r[event])[:10]<str(at.date())]
        if not matches:facts[group+'.status']='missing_historical_context';continue
        r=max(matches,key=lambda r:str(r[event]));receipts[group]=r.get(receipt) if receipt else None
        facts[group+'.status']='reconstructed_prior_session_availability_unverified';facts[group+'.event_at']=str(r[event])
        for k in fields:
            v=r.get(k)
            if v is None or isinstance(v,(str,bool,int)) or isinstance(v,float) and math.isfinite(v):facts[group+'.'+k]=v
    facts.update(context_version='prior-session-research-v2',phase='next_session_watchlist',underlying=symbol)
    return facts,receipts


def context_split(row):
    at=stamp(row['decision_at']);end=stamp(row['label_available_at'])
    if not date(2026,9,1)<=at.date()<date(2026,10,24):return None
    if end<at:raise ValueError('Outcome precedes input')
    if at.date()<date(2026,10,19):return 'train' if end.date()<date(2026,10,19) else 'purged'
    if at.date()<date(2026,10,22):return 'valid' if end.date()<date(2026,10,22) else 'purged'
    return 'test' if end.date()<date(2026,10,27) else 'purged'


def write(records,output,quality,source_hashes,exclusions):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest={'quality_tier':quality,'live_promotion_allowed':False,'sources':source_hashes,'exclusions':exclusions,'lanes':{}}
    for lane in sorted({r['lane'] for r in records}):
        folder=output/lane;folder.mkdir();parts=defaultdict(list)
        for r in sorted(records,key=lambda r:(r['decision_at'],r['id'])):
            name=context_split(r)
            if name:parts[name].append(r)
        for name in ('train','valid','test','purged'):
            (folder/(name+'.records.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True,allow_nan=False)+'\n' for r in parts[name]))
            if name!='purged':(folder/(name+'.jsonl')).write_text(''.join(json.dumps(message(r),sort_keys=True,allow_nan=False)+'\n' for r in parts[name]))
        manifest['lanes'][lane]={n:{'rows':len(parts[n]),'positive':sum(r['net_return']>.005 for r in parts[n]),'negative':sum(r['net_return']<-.005 for r in parts[n])} for n in ('train','valid','test','purged')}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));return manifest


def enrich_research(source,market_path,output):
    """Enrich a frozen reconstructed corpus without changing any split or label.

    Receipt audit stays outside model facts. Choose a new output directory;
    this never replaces a corpus already used for evaluation.
    """
    from .policy import SYSTEM_SHA256
    source=Path(source);market_path=Path(market_path);output=Path(output)
    manifest=json.loads((source/'manifest.json').read_text())
    if 'reconstruct' not in manifest.get('quality_tier',''):
        raise ValueError('This enrichment is research-only; verified inputs need their own receipt audit')
    market=json.loads(market_path.read_text());output.mkdir(parents=True,exist_ok=False)
    fingerprints={}
    for lane in manifest['lanes']:
        folder=output/lane;folder.mkdir()
        for part in ('train','valid','test','purged'):
            path=source/lane/(part+'.records.jsonl')
            records=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            if path.exists():fingerprints[str(path.relative_to(source))]=hashlib.sha256(path.read_bytes()).hexdigest()
            enriched=[]
            for row in records:
                if 'context_version' in row['features']:
                    raise ValueError('Use the original un-enriched frozen corpus, not a prior context export')
                facts,audit=prior_context(row,market)
                enriched.append({**row,'features':{**row['features'],**facts},'context_receipt_audit':audit,
                                 'historical_availability_verified':False})
            (folder/(part+'.records.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True,allow_nan=False)+'\n' for r in enriched))
            if part!='purged':(folder/(part+'.jsonl')).write_text(''.join(json.dumps(message(r),sort_keys=True,allow_nan=False)+'\n' for r in enriched))
    manifest.update(live_promotion_allowed=False,system_sha256=SYSTEM_SHA256,
        context_source_sha256=hashlib.sha256(market_path.read_bytes()).hexdigest(),frozen_record_sources=fingerprints,
        context_policy='Prior-session research only; preserve frozen split and label; receipt audit outside model inputs')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();inputs=p.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--raw');inputs.add_argument('--research-corpus');p.add_argument('--market-export');p.add_argument('--output',required=True);a=p.parse_args()
    if a.research_corpus:
        if not a.market_export:p.error('--market-export is required with --research-corpus')
        result=enrich_research(a.research_corpus,a.market_export,a.output)
    else:
        source=Path(a.raw);records,excluded=smart_entries(json.loads(source.read_text()))
        result=write(records,a.output,'recorded_preentry_snapshot',{source.name:hashlib.sha256(source.read_bytes()).hexdigest()},excluded)
    print(json.dumps(result,indent=2))
