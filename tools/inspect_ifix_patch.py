#!/usr/bin/env python3
"""List methods replaced by an Endfield InjectFix patch asset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ifix_patch import parse_injectfix_patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", type=Path)
    parser.add_argument(
        "--method",
        help="only include qualified method names containing this text",
    )
    args = parser.parse_args()

    patch = parse_injectfix_patch(args.patch.read_bytes())
    methods = [
        {
            "declaringType": method.declaring_type,
            "name": method.name,
            "parameters": list(method.parameters),
            "genericArguments": list(method.generic_arguments),
            "vmMethodId": method.vm_method_id,
        }
        for method in patch.patched_methods
        if args.method is None
        or args.method.casefold() in method.qualified_name.casefold()
    ]
    print(
        json.dumps(
            {
                "payloadOffset": patch.payload_offset,
                "assembly": patch.assembly,
                "vmMethodCount": patch.vm_method_count,
                "patchedMethodCount": len(patch.patched_methods),
                "matchedMethodCount": len(methods),
                "methods": methods,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
