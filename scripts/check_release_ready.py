from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_PARENT = PROJECT_ROOT.parent
if str(PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(PROJECT_PARENT))

from astrbot_plugin_qfarm.services.release_policy import validate_release_policy


def main() -> int:
    parser = argparse.ArgumentParser(description="Check release version/readme policy for astrbot_plugin_qfarm.")
    parser.add_argument(
        "--require-api-field",
        action="store_true",
        help="Require '- API:' item in latest README release block.",
    )
    args = parser.parse_args()

    errors = validate_release_policy(PROJECT_ROOT, require_api_field=bool(args.require_api_field))
    if errors:
        print("release policy check failed:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("release policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
