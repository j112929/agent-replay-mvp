"""Portable data bundles; no code loading or archive extraction side effects."""
import hashlib
import json
import stat
import zipfile
from pathlib import Path
from .storage import read,save
from .schema import validate_trace

def export_bundle(source, output):
    trace=validate_trace(read(source)); data=Path(source).read_bytes()
    manifest={'schema_version':'1.0','source_trace_id':trace['id'],'files':[{'path':'trace.json','sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}], 'replay_requirements':['Trusted local entrypoint and environment required']}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json.dumps(manifest)); z.writestr('trace.json',data)
    return manifest

def import_bundle(source,directory):
    with zipfile.ZipFile(source) as z:
        infos=z.infolist()
        if len(infos)>100 or sum(i.file_size for i in infos)>50*1024*1024: raise ValueError('Bundle exceeds limits')
        names=[i.filename for i in infos]
        if len(set(names))!=len(names) or set(names)!={'manifest.json','trace.json'}: raise ValueError('Unsupported bundle files')
        if any(stat.S_ISLNK(i.external_attr>>16) for i in infos): raise ValueError('Symlinks are forbidden')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('schema_version')!='1.0' or len(manifest.get('files',[]))!=1: raise ValueError('Invalid manifest')
        item=manifest['files'][0]; data=z.read('trace.json')
        if item.get('path')!='trace.json' or item.get('size')!=len(data) or item.get('sha256')!=hashlib.sha256(data).hexdigest(): raise ValueError('Bundle checksum mismatch')
        trace=validate_trace(json.loads(data))
        if trace['id']!=manifest.get('source_trace_id'): raise ValueError('Bundle source mismatch')
    # Content address prevents untrusted IDs from becoming paths.
    target=Path(directory)/'traces'/(hashlib.sha256(data).hexdigest()+'.json')
    save(target,trace)
    return {'path':str(target),'trace_id':trace['id']}
