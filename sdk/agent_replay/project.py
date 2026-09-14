"""Only a user-selected local project registry can select executable code."""
import importlib
from pathlib import Path
from .storage import read,within

def load_project(path):
    path=Path(path).resolve(); data=read(path)
    if data.get('schema_version')!='1.0': raise ValueError('Unsupported project version')
    data['_root']=str(within(path.parent,data.get('project_root','.')))
    data['_path']=str(path)
    return data

def callable_for(project,group,key):
    entry=project.get(group,{}).get(key)
    if not isinstance(entry,dict) or not isinstance(entry.get('callable'),str): raise ValueError('Unregistered '+group+' ID: '+str(key))
    module,name=entry['callable'].split(':',1)
    fn=getattr(importlib.import_module(module),name)
    if not callable(fn): raise ValueError('Registered entry is not callable')
    return fn
