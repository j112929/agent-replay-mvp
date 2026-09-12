"""Build a credential-free source ZIP and bundle the local UI for Python wheels."""
from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
assets = ["index.html", "styles.css", "app.js", "core.js", "demo.js"]
web = root / "sdk" / "agent_replay" / "web"
web.mkdir(exist_ok=True)
for name in assets:
    shutil.copy2(root / "dist" / name, web / name)
output = root / "dist" / "agent-replay-mvp.zip"
excluded = {".git", ".openai", ".sites-runtime", ".venv", "node_modules", "__pycache__", ".replay", ".env", ".DS_Store"}
paths = []
for folder in ("sdk", "dist", "examples", "tests", "schema", "scripts"):
    for path in sorted((root / folder).rglob("*")):
        if not path.is_file() or path == output:
            continue
        if any(part in excluded or part.endswith(".egg-info") for part in path.relative_to(root).parts):
            continue
        if path.suffix in (".pyc", ".zip", ".whl"):
            continue
        paths.append(path)
for name in ("README.md", "LICENSE", "pyproject.toml", "package.json", "vercel.json", ".gitignore"):
    paths.append(root / name)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in paths:
        archive.write(path, "agent-replay-mvp/" + path.relative_to(root).as_posix())
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    assert "agent-replay-mvp/README.md" in archive.namelist()
    assert "agent-replay-mvp/sdk/agent_replay/web/index.html" in archive.namelist()
print(f"Created {output.name}: {len(paths)} files, {output.stat().st_size:,} bytes")
