"""Loopback-only HTTP runner and command-line replay."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from . import __version__
from .schema import validate_trace, write_trace
from .replay import replay_step

LIMIT = 5 * 1024 * 1024

def load_trace(path):
    file = Path(path)
    if file.stat().st_size > LIMIT:
        raise ValueError("Trace exceeds 5 MB")
    return validate_trace(json.loads(file.read_text(encoding="utf-8")))

def web_directory():
    packaged = Path(__file__).resolve().parent / "web"
    source = Path(__file__).resolve().parents[2] / "dist"
    if source.joinpath("index.html").is_file():
        return source
    if packaged.joinpath("index.html").is_file():
        return packaged
    raise RuntimeError("Web assets not found. Run from the full source download or use a packaged wheel.")

def make_handler(trace_directory, web_root):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def log_message(self, fmt, *args):
            # Never log bodies, captured content, or provider credentials.
            pass

        def send_json(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in hosts or (origin is not None and origin != f"http://{host}"):
                self.send_json({"error": "Only same-origin loopback requests are accepted"}, 403)
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            path = urlsplit(self.path).path
            if path == "/api/health":
                return self.send_json({"service": "agent-replay", "version": __version__})
            if path == "/api/traces":
                traces = []
                paths = sorted(Path(trace_directory).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
                for file in paths:
                    try:
                        traces.append(load_trace(file))
                    except (ValueError, OSError):
                        continue
                return self.send_json(traces)
            if path.startswith("/api/"):
                return self.send_json({"error": "Unknown API route"}, 404)
            # Only serve bundled public assets. No directory browsing or trace files.
            allowed = {"/", "/index.html", "/styles.css", "/app.js", "/core.js", "/demo.js", "/agent-replay-mvp.zip"}
            if path not in allowed:
                return self.send_json({"error": "Not found"}, 404)
            return super().do_GET()

        def do_HEAD(self):
            if not self.local_request():
                return
            allowed = {"/", "/index.html", "/styles.css", "/app.js", "/core.js", "/demo.js", "/agent-replay-mvp.zip"}
            if urlsplit(self.path).path not in allowed:
                self.send_error(404)
                return
            return super().do_HEAD()

        def do_POST(self):
            if not self.local_request():
                return
            if urlsplit(self.path).path != "/api/replay":
                return self.send_json({"error": "Unknown API route"}, 404)
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                return self.send_json({"error": "Content-Type must be application/json"}, 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > LIMIT:
                    return self.send_json({"error": "Replay request must be between 1 byte and 5 MB"}, 413)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
                model = payload.get("model")
                if not isinstance(model, str) or not model.strip() or len(model) > 200:
                    raise ValueError("A model ID is required (max 200 characters)")
                result = replay_step(payload["trace"], payload["step_id"], mode="live", model=model)
                write_trace(result, trace_directory)
                self.send_json(result)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
            except OSError:
                self.send_json({"error": "Could not persist replay in the trace directory"}, 500)

    return Handler

def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent-replay", description="Capture, inspect, and replay agent failures locally.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    server_cmd = commands.add_parser("serve", help="Serve the debugger on loopback")
    server_cmd.add_argument("--port", type=int, default=8765)
    server_cmd.add_argument("--directory", default=".replay/traces")
    replay_cmd = commands.add_parser("replay", help="Replay one recorded step")
    replay_cmd.add_argument("trace")
    replay_cmd.add_argument("--step", required=True, help="Exact step ID from the trace")
    mode = replay_cmd.add_mutually_exclusive_group()
    mode.add_argument("--model", help="Live replay with an OpenAI-compatible model")
    mode.add_argument("--fixture", help="Path to a JSON tool-output fixture")
    mode.add_argument("--tool", help="Explicit local module:function to execute with recorded tool inputs")
    replay_cmd.add_argument("--base-url", help="Override provider API base URL")
    replay_cmd.add_argument("--directory", default=".replay/traces")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            Path(args.directory).mkdir(parents=True, exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.directory, web_directory()))
            print(f"Replay v{__version__} → http://127.0.0.1:{server.server_port}", flush=True)
            print(f"Trace directory: {Path(args.directory).resolve()}", flush=True)
            print("Live models use OPENAI_API_KEY / OPENAI_BASE_URL from this process environment.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        trace = load_trace(args.trace)
        fixture, function = None, None
        replay_mode = "recorded"
        if args.fixture:
            fixture = json.loads(Path(args.fixture).read_text())
            replay_mode = "fixture"
        elif args.model:
            replay_mode = "live"
        elif args.tool:
            module, name = args.tool.split(":", 1)
            function = getattr(importlib.import_module(module), name)
            replay_mode = "tool"
        result = replay_step(trace, args.step, mode=replay_mode, fixture=fixture, model=args.model, base_url=args.base_url, tool_fn=function)
        path = write_trace(result, args.directory)
        print(json.dumps({"status": result["status"], "mode": replay_mode, "path": str(path)}))
        return 1 if result["status"] == "error" else 0
    except (ValueError, OSError, ImportError, AttributeError) as exc:
        print(f"agent-replay: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
