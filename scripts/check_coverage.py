"""Gate each critical runtime module, never combined test/migration coverage."""

import json
import sys
from pathlib import Path

CRITICAL = [
    "apps/accounts/permissions.py",
    "apps/imports/views.py",
    "apps/programmes/services/tier_engine.py",
    "apps/reporting/services/kpis.py",
]


def main(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = {key.replace(chr(92), "/"): item for key, item in data["files"].items()}
    failed = False
    for name in CRITICAL:
        item = files.get(name)
        percent = item["summary"]["percent_covered"] if item else 0
        print(f"{name}: {percent:.1f}% (minimum 70%)")
        failed |= percent < 70
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
