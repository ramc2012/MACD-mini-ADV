"""Recover metadata only from observed sibling strikes sharing an expiry code."""
import collections,json,re
from study import ROOT
original=json.loads((ROOT/'expiries.json').read_text())
pattern=re.compile(r'^(.*?26)(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|[1-9OND]\d{2})')
group=collections.defaultdict(set)
for sym,expiry in original.items():
    match=pattern.match(sym)
    if match and expiry:group[match.group(0)].add(expiry)
resolved={};provenance={}
for sym,expiry in original.items():
    match=pattern.match(sym)
    dates=group.get(match.group(0),set()) if match else set()
    if not expiry and len(dates)==1:
        expiry=next(iter(dates));provenance[sym]={'method':'same exchange, underlying and contract expiry code','key':match.group(0),'expiry':expiry}
    resolved[sym]=expiry
(ROOT/'resolved_expiries.json').write_text(json.dumps(resolved,indent=2))
(ROOT/'expiry_provenance.json').write_text(json.dumps(provenance,indent=2))
print({'direct':sum(bool(v) for v in original.values()),'sibling_resolved':len(provenance),'unresolved':sum(not v for v in resolved.values())})
