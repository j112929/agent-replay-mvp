"""Server-configured pilot identities and tenant-scoped audit/retention."""
import datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import shutil
import threading
import time
from ..storage import read, within
from .sanitizer import sanitize


class AccessDenied(ValueError):
    pass


class AccessControl:
    def __init__(self, root, config):
        self.root = Path(root).resolve()
        self.identities = config.get('identities', [])
        self.retention_days = config.get('retention_days', 30)
        self.lock = threading.Lock()
        if type(self.retention_days) is not int or self.retention_days < 1:
            raise ValueError('Invalid retention days')
        if not self.identities:
            raise ValueError('At least one configured identity is required')
        hashes = set()
        for identity in self.identities:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identity.get('org_id','')) or identity.get('role') not in ('ADMIN','DEVELOPER','VIEWER'):
                raise ValueError('Invalid configured organization or role')
            token_hash = identity.get('token_sha256','')
            if not re.fullmatch(r'[a-f0-9]{64}',token_hash) or token_hash in hashes or not identity.get('subject'):
                raise ValueError('Invalid or duplicate identity digest')
            hashes.add(token_hash)

    def authenticate(self, header):
        if not header or not header.startswith('Bearer '):
            raise AccessDenied('Authentication required')
        raw = header[7:]
        if len(raw) < 32 or len(raw) > 4096:
            raise AccessDenied('Authentication required')
        digest = hashlib.sha256(raw.encode()).hexdigest()
        identity = next((i for i in self.identities if hmac.compare_digest(i['token_sha256'],digest)),None)
        if identity is None:
            raise AccessDenied('Authentication required')
        return {k:identity[k] for k in ('subject','org_id','role')}

    def directory(self, principal):
        base=within(self.root,'organizations')
        path=within(base,principal['org_id'])
        # Do not permit aliases linking two organizations, even inside the root.
        if (self.root/'organizations').is_symlink() or (self.root/'organizations'/principal['org_id']).is_symlink():
            raise AccessDenied('Tenant storage aliases are forbidden')
        path.mkdir(parents=True,exist_ok=True,mode=0o700)
        return path

    def authorize(self, principal, method, path):
        if path.startswith('/api/') and not path.startswith('/api/v1/'):
            raise AccessDenied('Legacy API disabled in organization mode')
        if method not in ('GET','HEAD') and principal['role']=='VIEWER':
            raise AccessDenied('Viewer cannot modify data')
        if any(x in path for x in ('/retention','/policies','/admin','/accept')) and principal['role']!='ADMIN':
            raise AccessDenied('Administrator required')

    def audit(self, principal, action, outcome='allowed', resource=None):
        directory=within(self.root,'audit');directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        record={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **principal,'action':action,'outcome':outcome,'resource':resource}
        # No request body, headers, raw errors or bearer values enter the audit record.
        with self.lock:
            with (directory/(principal['org_id']+'.jsonl')).open('a') as f:
                f.write(json.dumps(sanitize(record),sort_keys=True)+'\n')

    def retain(self, principal, apply=False):
        self.authorize(principal,'POST','/api/v1/retention')
        root=self.directory(principal)
        cutoff=time.time()-self.retention_days*86400
        files=[p for p in root.rglob('*') if p.is_file()]
        # Atomic tenant cohort semantics: derived evidence cannot outlive deleted sources.
        eligible=bool(files) and all(p.stat().st_mtime < cutoff for p in files)
        result={'eligible':eligible,'dry_run':not apply,'retention_days':self.retention_days,
                'files':len(files) if eligible else 0,'scope':'entire inactive organization artifact cohort'}
        self.audit(principal,'retention.apply' if apply else 'retention.preview')
        if apply and eligible:
            for child in list(root.iterdir()):
                if child.is_symlink():child.unlink()
                elif child.is_dir():shutil.rmtree(child)
                else:child.unlink()
        return result
