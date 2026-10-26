"""Sequential research training only; never selects production model weights."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


def main():
    root=Path('runtime/qwen-training')
    with (root/'plan.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=json.loads((root/'final-training-plan.json').read_text());states={}
        if plan.get('objective')!='verdict':raise ValueError('Only the current constrained-verdict plan is supported')
        for config in plan['configs']:
            c=json.loads(Path(config).read_text());lane=Path(c['data']).name
            folder=Path(c['adapter_path']);receipt=folder/'training_receipt.json'
            if receipt.exists():
                saved=json.loads(receipt.read_text())
                if saved.get('complete') and saved.get('adapter_sha256')==hashlib.sha256((folder/'adapters.safetensors').read_bytes()).hexdigest():
                    states[lane]={'state':'trained','receipt':str(receipt)}
                    (root/'plan-state.json').write_text(json.dumps(states,indent=2))
                    continue
            if folder.exists():raise ValueError(f'{lane}: preserve incomplete checkpoint and choose a fresh output/config before restarting')
            states[lane]={'state':'training','at':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}
            (root/'plan-state.json').write_text(json.dumps(states,indent=2))
            with (root/(lane+'-'+plan.get('run_id','final')+'-training.log')).open('x') as log:
                result=subprocess.run([sys.executable,'local_decisions/train_verdict.py','--config',config],stdout=log,stderr=subprocess.STDOUT)
            states[lane]={'state':'trained' if result.returncode==0 else 'checkpointed_cutoff' if result.returncode==75 else 'failed',
                          'returncode':result.returncode,'at':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}
            (root/'plan-state.json').write_text(json.dumps(states,indent=2))
            print(lane,states[lane],flush=True)
            if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':main()
