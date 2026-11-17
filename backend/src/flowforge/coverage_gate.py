"""Enforce coverage on each implemented domain, including missing report entries."""

import json
import sys
from pathlib import Path


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    failed = False
    for domain in ("auth/", "projects/", "contracts.py"):
        summaries = [
            item["summary"]
            for name, item in report["files"].items()
            if "/flowforge/" + domain in "/" + name.replace("\\", "/")
        ]
        total = sum(item["num_statements"] for item in summaries)
        covered = sum(item["covered_lines"] for item in summaries)
        percent = covered / total * 100 if total else 0
        print(f"{domain}: {percent:.1f}% ({covered}/{total}), required >=80%")
        failed |= percent < 80
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
