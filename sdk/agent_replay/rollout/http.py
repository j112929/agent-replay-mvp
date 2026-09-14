"""Role-scoped HTTP protocol; run behind TLS for remote workers."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .store import Conflict

ROUTES = {
    'policy': ('actor verifier learner admin', 'policy'),
    'trajectory': ('verifier learner admin', 'trajectory'),
    'claim': ('actor verifier learner admin', 'claim'),
    'heartbeat': ('actor verifier learner admin', 'heartbeat'),
    'fail': ('actor verifier learner admin', 'fail'),
    'complete': ('actor admin', 'complete'),
    'verify': ('verifier admin', 'verify'),
    'publish': ('learner admin', 'publish'),
    'initialize': ('admin', 'initialize'),
    'enqueue': ('admin', 'enqueue'),
    'batch': ('admin', 'batch'),
    'snapshot': ('admin', 'snapshot'),
    'scheduler_status': ('admin', 'scheduler_status'),
    'metrics': ('admin', 'metrics'),
    'measure': ('actor verifier learner admin', 'measure'),
}


def make_server(store, tokens, host='127.0.0.1', port=8877):
    if set(tokens) != {'admin', 'actor', 'verifier', 'learner'} or any(not isinstance(t, str) or len(t) < 24 for t in tokens.values()) or len(set(tokens.values())) != 4:
        raise ValueError('Four distinct role tokens, each at least 24 characters, are required')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            data = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/metrics' or self.headers.get('Authorization') != 'Bearer '+tokens['admin']:
                return self.reply({'error': 'Unauthorized'}, 401)
            from .metrics import prometheus
            data = prometheus(store.metrics()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.connection.settimeout(15)
            token = self.headers.get('Authorization', '').removeprefix('Bearer ')
            role = next((r for r, t in tokens.items() if hmac.compare_digest(t.encode(), token.encode())), None)
            if role is None:
                return self.reply({'error': 'Unauthorized'}, 401)
            # Browser origins are deliberately unsupported; this is a worker API.
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
                return self.reply({'error': 'Unsupported request'}, 403)
            action = self.path.removeprefix('/api/rollout/v1/')
            route = ROUTES.get(action) if self.path.startswith('/api/rollout/v1/') else None
            if route is None:
                return self.reply({'error': 'Unknown route'}, 404)
            if role not in route[0].split():
                return self.reply({'error': 'Wrong worker role'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 5*1024*1024 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Expected JSON body up to 5 MB')
                args = json.loads(self.rfile.read(size))
                if not isinstance(args, dict):
                    raise ValueError('Expected object')
                if action in ('claim', 'fail') and role != 'admin' and args.get('kind') != role:
                    return self.reply({'error': 'Cannot claim another role'}, 403)
                if action == 'measure' and role != 'admin' and args.get('role') != role:
                    return self.reply({'error': 'Cannot report another role'}, 403)
                # Heartbeats require a capability token already obtained by claiming a job.
                result = getattr(store, route[1])(**args)
                self.reply({'result': result})
            except Conflict as exc:
                self.reply({'error': str(exc)}, 409)
            except (ValueError, TypeError, KeyError) as exc:
                self.reply({'error': str(exc)}, 400)
            except Exception:
                self.reply({'error': 'Internal controller error'}, 500)

    return ThreadingHTTPServer((host, port), Handler)


class Client:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Expected HTTP(S) controller URL without credentials')
        if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Remote controllers require HTTPS')
        self.url = url.rstrip('/')
        self.token = token

    def call(self, action, **args):
        request = Request(self.url+'/api/rollout/v1/'+action, json.dumps(args, allow_nan=False).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer '+self.token})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)['result']
        except HTTPError as exc:
            try:
                message = json.load(exc).get('error', str(exc))
            finally:
                exc.close()
            raise (Conflict if exc.code == 409 else ValueError)(message) from exc

    def __getattr__(self, action):
        if action not in ROUTES:
            raise AttributeError(action)
        return lambda **args: self.call(action, **args)
