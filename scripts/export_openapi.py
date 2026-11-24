"""Export FlowForge's OpenAPI contract without starting the application lifespan."""

import argparse
import json
from pathlib import Path

from flowforge.api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    schema = create_app().openapi()
    content = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
