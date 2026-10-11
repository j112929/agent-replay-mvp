"""Verifier protocol: workspace argument; JSON verdict on stdout; exit 0."""
import json
from pathlib import Path
import sys
p = Path(sys.argv[1]) / "result.txt"
success = p.is_file() and p.read_text() == "hello agent"
print(json.dumps({"success": success, "score": float(success)}))
