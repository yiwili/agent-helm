"""External acceptance example for a synthetic message.txt task.

Pin this file outside the worker's source/state directories. It only checks data;
it does not import or execute candidate code. No third-party dependencies.
"""
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])/"message.txt"
passed = path.is_file() and path.read_text(encoding="utf-8") == "fixed"
print(json.dumps({"acceptance": "message.txt contains fixed", "passed": passed}))
raise SystemExit(0 if passed else 1)
