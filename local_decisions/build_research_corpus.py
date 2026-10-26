"""Separate, explicitly unverified-availability reconstructed research corpora.

These do not replace or relax the verified corpus. Splits use economic outcome
maturity, while real receipt timestamps remain retained and disclosed.
"""
from collections import Counter,defaultdict
from datetime import date
import argparse
import hashlib
import json
from pathlib import Path

from .build_corpus import message,stamp
from .policy import SYSTEM_SHA256


def split_research(row):
    day=stamp(row['decision_at']).date();end=stamp(row['label_matures_at']).date()
    valid=date(2026,10,12);test=date(2026,10,14) if row['lane']=='vanguard' else date(2026,10,19)
    if end<day:raise ValueError('Future label ordering invalid')
    if day<valid:return 'train' if end<valid else 'purged'
    if day<test:return 'valid' if end<test else 'purged'
    return 'test' if end<date(2026,10,24) else 'purged'


def build(path,output):
    path=Path(path);output=Path(output);output.mkdir(parents=True,exist_ok=False);j=json.loads(path.read_text())
    manifest={'quality_tier':'reconstructed_unverified_availability','live_promotion_allowed':False,
              'system_sha256':SYSTEM_SHA256,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'split_basis':'Event-time outcome maturity, not proof of historical receipt/availability',
              'excluded':j['excluded'],'lanes':{}}
    for lane in ('vanguard','cbe'):
        folder=output/lane;folder.mkdir();parts=defaultdict(list)
        for row in sorted((r for r in j['records'] if r['lane']==lane),key=lambda r:(r['decision_at'],r['id'])):
            parts[split_research(row)].append(row)
        for name in ('train','valid','test','purged'):
            (folder/(name+'.records.jsonl')).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in parts[name]))
            if name!='purged':(folder/(name+'.jsonl')).write_text(''.join(json.dumps(message(r),sort_keys=True)+'\n' for r in parts[name]))
        manifest['lanes'][lane]={name:{'rows':len(parts[name]),'positive':sum(r['net_return']>.005 for r in parts[name]),'negative':sum(r['net_return']<-.005 for r in parts[name])} for name in ('train','valid','test','purged')}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args();build(a.source,a.output)
