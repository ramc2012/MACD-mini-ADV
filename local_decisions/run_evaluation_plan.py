"""Run/resume all frozen research evaluations serially without serving changes."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path('runtime/qwen-training')
    for lane in ('vanguard', 'cbe', 'smart'):
        config = json.loads(Path('local_decisions',lane+'_verdict_mixed.json').read_text())
        folder = Path(config['adapter_path'])
        receipt = json.loads((folder/'training_receipt.json').read_text())
        if not receipt.get('complete'):raise ValueError('Training incomplete: '+lane)
        output = root/('evaluation-verdict-mixed-'+lane)
        with (root/(lane+'-verdict-evaluation.log')).open('a') as log:
            result = subprocess.run([sys.executable,'local_decisions/evaluate_local.py',
                '--base',config['model'],'--adapter',str(folder),'--corpus',config['data'],
                '--output',str(output),'--resume'],stdout=log,stderr=subprocess.STDOUT)
        print(lane,'evaluation returncode',result.returncode,flush=True)
        if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':main()
