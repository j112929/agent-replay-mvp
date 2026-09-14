"""Trusted project worker protocol. Trace data never selects a callable."""
import os
import sys
from pathlib import Path
from ..storage import read,save
from ..runtime.runner import run

def main():
    request=read(sys.argv[1]); project=request['project']
    os.chdir(project['_root']);sys.path.insert(0,project['_root'])
    try:
        result=run(request['trace'],request['spec'],project,request['directory'])
        save(sys.argv[2],{'trace':result})
    except Exception as exc:
        save(sys.argv[2],{'error':{'type':type(exc).__name__,'message':str(exc)}})
        return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
