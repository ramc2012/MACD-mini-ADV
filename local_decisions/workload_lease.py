"""Publish the active native research workload; never load a model or alter OS settings."""
from datetime import datetime,timedelta,timezone
import json,shlex,subprocess,time
from pathlib import Path


def main():
 path=Path('runtime/local_decisions/model_workload.json');path.parent.mkdir(parents=True,exist_ok=True)
 while True:
  jobs=[]
  for line in subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines():
   parts=line.strip().split(None,1)
   if len(parts)!=2:continue
   try:args=shlex.split(parts[1])
   except ValueError:continue
   if len(args)>1 and args[1] in ('local_decisions/run_context_plan.py','local_decisions/train_verdict.py','local_decisions/evaluate_local.py','local_decisions/json_sanity.py'):
    jobs.append({'pid':int(parts[0]),'job':Path(args[1]).stem})
  now=datetime.now(timezone.utc)
  payload={'active':bool(jobs),'jobs':jobs,'observed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=3)).isoformat()}
  temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(payload));temporary.replace(path)
  if not jobs:return
  time.sleep(10)


if __name__=='__main__':main()
