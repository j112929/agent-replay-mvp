"""Durable event prefixes. Interrupted writes never imply completed execution."""
import json
import os
import threading
from pathlib import Path
from .serialization import digest
from .storage import save
from .migrations import to_v2

class Journal:
    def __init__(self, path, durability='buffered'):
        if durability not in ('buffered','sync'): raise ValueError('Unknown durability')
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('x'); self.seq=0; self.durable_seq=0; self.lock=threading.Lock(); self.durability=durability
    def append(self, kind, payload):
        with self.lock:
            self.seq+=1
            record={'schema_version':'1.0','seq':self.seq,'type':kind,'payload':payload}
            record['checksum']=digest(record)
            self.stream.write(json.dumps(record,ensure_ascii=False)+'\n'); self.stream.flush()
            if self.durability=='sync' or kind in ('step_failed','run_finished'):
                os.fsync(self.stream.fileno()); self.durable_seq=self.seq
    def close(self): self.stream.close()

def recover(path, output=None):
    records=[]; gaps=[]
    lines=Path(path).read_bytes().splitlines(keepends=True)
    for n,line in enumerate(lines):
        try:
            r=json.loads(line); checksum=r.pop('checksum')
            if digest(r)!=checksum or r['seq']!=n+1: raise ValueError('checksum or sequence mismatch')
            records.append(r)
        except (ValueError,KeyError,TypeError):
            gaps.append('Incomplete tail' if n==len(lines)-1 and not line.endswith(b'\n') else 'Corrupt journal at record '+str(n+1)); break
    if not records or records[0]['type']!='run_started': raise ValueError('No recoverable run header')
    trace=records[0]['payload']; steps={}
    finished=False
    for r in records[1:]:
        if r['type'].startswith('step_'): steps[r['payload']['id']]=r['payload']
        elif r['type']=='run_output': trace['output']=r['payload']
        elif r['type']=='failure_observed': trace.setdefault('failures',[]).append(r['payload'])
        elif r['type']=='run_finished': trace=r['payload']; finished=True
    if not finished:
        trace['steps']=list(steps.values()); trace['status']='error'
    result=to_v2(trace)
    if not finished:
        result['execution']['status']='interrupted'
        for s in result['steps']:
            if s['execution']['status']=='running': s['execution']['status']='interrupted'
    result['capture_health']={'state':'complete' if finished and not gaps else 'partial','gaps':gaps+([] if finished else ['Unfinished external effects are unknown']),'last_recovered_seq':len(records)}
    if output: save(output,result)
    return result
