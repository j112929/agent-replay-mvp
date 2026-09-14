"""Content-addressed trajectory objects and verified, immutable checkpoint manifests."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from ..serialization import canonical, digest
from ..storage import save


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


class LocalObjects:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
            raise ValueError('Invalid content hash')
        return self.root/key[:2]/key

    def put(self, value):
        key = digest(value)
        path = self.path(key)
        if path.exists():
            self.get(key)
        else:
            save(path, value)
        return key

    def get(self, key):
        data = self.path(key).read_bytes()
        value = json.loads(data)
        if digest(value) != key:
            raise ValueError('Artifact checksum mismatch')
        return value


class S3Objects:
    """Optional S3-compatible object transport; credentials use boto3's normal chain."""
    def __init__(self, bucket, prefix='rollout/', endpoint_url=None):
        import boto3
        self.client = boto3.client('s3', endpoint_url=endpoint_url)
        self.bucket, self.prefix, self.endpoint_url = bucket, prefix, endpoint_url

    def _key(self, key):
        if not re.fullmatch('[a-f0-9]{64}', key):
            raise ValueError('Invalid content hash')
        return self.prefix+key

    def put(self, value):
        key = digest(value)
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=canonical(value).encode(), ContentType='application/json')
        return key

    def get(self, key):
        body = self.client.get_object(Bucket=self.bucket, Key=self._key(key))['Body']
        try:
            value = json.loads(body.read())
        finally:
            body.close()
        if digest(value) != key:
            raise ValueError('Artifact checksum mismatch')
        return value


def checkpoint_manifest(directory):
    directory = Path(directory).resolve()
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Checkpoint symlinks are not allowed')
        if path.is_file() and path.name != 'manifest.json':
            files[path.relative_to(directory).as_posix()] = file_sha(path)
    if not files:
        raise ValueError('Empty checkpoint')
    return {'format': 'checkpoint.v1', 'files': files}


def seal_checkpoint(directory):
    manifest = checkpoint_manifest(directory)
    save(Path(directory)/'manifest.json', manifest)
    return {'format': 'hf-checkpoint.v1', 'path': str(Path(directory).resolve()), 'manifest_digest': digest(manifest)}


def verify_checkpoint(reference, root):
    root = Path(root).resolve()
    directory = Path(reference['path']).resolve()
    if directory == root or root not in directory.parents:
        raise ValueError('Checkpoint must be inside the configured shared root')
    manifest = json.loads((directory/'manifest.json').read_text())
    if digest(manifest) != reference['manifest_digest'] or checkpoint_manifest(directory) != manifest:
        raise ValueError('Checkpoint content changed')
    return directory


def backup(store, output):
    """Online SQLite snapshot plus all external trajectory objects it references."""
    import sqlite3
    target = Path(output).resolve()
    target.mkdir(parents=True, exist_ok=False)
    db_path = target/'controller.sqlite3'
    source = sqlite3.connect(store.path)
    destination = sqlite3.connect(db_path)
    try:
        source.backup(destination)
        refs = []
        objects = LocalObjects(target/'objects')
        for (data,) in destination.execute('SELECT data FROM trajectories'):
            value = json.loads(data)
            if '$artifact' in value:
                objects.put(store.objects.get(value['$artifact']))
                refs.append(value['$artifact'])
    finally:
        source.close()
        destination.close()
    os.chmod(db_path, 0o600)
    save(target/'backup.json', {'format': 'controller-backup.v1', 'database_sha256': file_sha(db_path), 'objects': sorted(set(refs)), 'checkpoint_policy': 'external immutable checkpoints must be retained separately'})
    return str(target)


def restore(source, database, objects_root):
    import sqlite3
    source, database = Path(source).resolve(), Path(database).resolve()
    manifest = json.loads((source/'backup.json').read_text())
    if manifest.get('format') != 'controller-backup.v1' or file_sha(source/'controller.sqlite3') != manifest['database_sha256']:
        raise ValueError('Backup checksum mismatch')
    if database.exists():
        raise ValueError('Restore requires a new database path')
    original = LocalObjects(source/'objects')
    values = [original.get(key) for key in manifest['objects']]
    objects = LocalObjects(objects_root)
    for value in values:
        objects.put(value)
    database.parent.mkdir(parents=True, exist_ok=True)
    with (source/'controller.sqlite3').open('rb') as inp, database.open('xb') as out:
        shutil.copyfileobj(inp, out)
    os.chmod(database, 0o600)
    with sqlite3.connect(database) as db:
        # Old workers must not retain authority after a controller restore.
        db.execute("UPDATE jobs SET state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,token=NULL,owner=NULL,deadline=NULL WHERE state='leased'")
        db.execute("INSERT OR REPLACE INTO settings VALUES('objects_root',?)", (str(Path(objects_root).resolve()),))
        db.execute("INSERT OR REPLACE INTO settings VALUES('objects_config',?)", (json.dumps({'type':'local','root':str(Path(objects_root).resolve())}),))
    return str(database)
