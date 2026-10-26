"""Read-only extraction of paper signal/trade records; never imports the engine."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def export(path,output,name):
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    out={'source':name,'database':str(path),'captured_at':datetime.now(timezone.utc).isoformat(),'query_only':True,'tables':{}}
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('signals','orders','trades','closed_positions','smart_journal','smart_positions'):
        if table in names:out['tables'][table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
    db.rollback();db.close()
    raw=json.dumps(out,sort_keys=True,default=str).encode();Path(output).write_bytes(raw)
    print(json.dumps({'source':name,'rows':{t:len(r) for t,r in out['tables'].items()},'sha256':hashlib.sha256(raw).hexdigest()}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('path');p.add_argument('output');p.add_argument('name');a=p.parse_args();export(a.path,a.output,a.name)
