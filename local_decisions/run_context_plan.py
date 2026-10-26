"""Sequential after-close research training and complete held-out comparisons."""
from pathlib import Path
import hashlib,json,subprocess,sys
from datetime import datetime
from zoneinfo import ZoneInfo


def main():
    root=Path('runtime/qwen-context-v2');states={}
    for lane in ('smart','vanguard','cbe'):
        config=root/(lane+'-training.json');c=json.loads(config.read_text());folder=Path(c['adapter_path'])
        states[lane]={'state':'training','started_at':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()}
        (root/'plan-state.json').write_text(json.dumps(states,indent=2))
        if not (folder/'training_receipt.json').exists():
            if folder.exists():raise ValueError('Preserve incomplete adapter and choose a fresh config/output: '+lane)
            with (root/(lane+'-balanced-training.log')).open('x') as log:
                result=subprocess.run([sys.executable,'local_decisions/train_verdict.py','--config',str(config)],stdout=log,stderr=subprocess.STDOUT)
            if result.returncode:raise SystemExit(result.returncode)
        receipt=json.loads((folder/'training_receipt.json').read_text())
        if not receipt.get('complete') or receipt['adapter_sha256']!=hashlib.sha256((folder/'adapters.safetensors').read_bytes()).hexdigest():raise ValueError('Training receipt mismatch')
        states[lane]={'state':'evaluation','training_complete':True};(root/'plan-state.json').write_text(json.dumps(states,indent=2))
        with (root/(lane+'-evaluation.log')).open('a') as log:
            result=subprocess.run([sys.executable,'local_decisions/evaluate_local.py','--base',c['model'],'--adapter',str(folder),
                    '--corpus',c['data'],'--output',str(root/(lane+'-evaluation')),'--resume'],stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise SystemExit(result.returncode)
        states[lane]={'state':'research_evaluated','training_complete':True,'evaluation_complete':True,'deployed':False}
        (root/'plan-state.json').write_text(json.dumps(states,indent=2));print(lane,states[lane],flush=True)


if __name__=='__main__':main()
