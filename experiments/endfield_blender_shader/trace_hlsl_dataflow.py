"""Trace dependencies in decompiled Endfield HLSL assignments.

This is intentionally a small static-analysis aid rather than a shader parser.
The archived FractalMiner HLSL uses one temporary assignment per line for most
of the CharacterNPR color path, which makes a conservative assignment graph
useful for auditing a recovered formula before porting it to Blender.

Example:
    python trace_hlsl_dataflow.py Sub0_Pass0_Fragment_b225.hlsl \
        --target _4148 --target _4150 --target _4152
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


ASSIGNMENT_RE = re.compile(
    r"^\s*(?:(?:const\s+)?(?:bool|float|float[234]|uint|int)\s+)?"
    r"(?P<target>_[A-Za-z0-9_]+)\s*=\s*(?P<expression>.+?);\s*$"
)
SYMBOL_RE = re.compile(r"\b_[A-Za-z0-9][A-Za-z0-9_]*\b")


@dataclass(frozen=True)
class Assignment:
    line_number: int
    expression: str


def parse_assignments(path: Path) -> dict[str, list[Assignment]]:
    assignments: dict[str, list[Assignment]] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        match = ASSIGNMENT_RE.match(line)
        if not match:
            continue
        assignments.setdefault(match.group("target"), []).append(
            Assignment(line_number, match.group("expression"))
        )
    return assignments


def trace_dependencies(
    assignments: dict[str, list[Assignment]], targets: list[str]
) -> tuple[set[str], set[str]]:
    temporaries: set[str] = set()
    external_inputs: set[str] = set()
    pending = list(targets)

    while pending:
        symbol = pending.pop()
        if symbol in temporaries or symbol in external_inputs:
            continue
        definitions = assignments.get(symbol)
        if not definitions:
            external_inputs.add(symbol)
            continue
        temporaries.add(symbol)
        for definition in definitions:
            for dependency in SYMBOL_RE.findall(definition.expression):
                if dependency != symbol:
                    pending.append(dependency)

    return temporaries, external_inputs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace decompiled Endfield HLSL temporary dependencies."
    )
    parser.add_argument("hlsl", type=Path)
    parser.add_argument("--target", action="append", required=True)
    args = parser.parse_args()

    assignments = parse_assignments(args.hlsl)
    temporaries, external_inputs = trace_dependencies(assignments, args.target)

    print("targets:")
    for target in args.target:
        print(f"  {target}")

    print("\nexternal inputs:")
    for symbol in sorted(external_inputs):
        print(f"  {symbol}")

    print("\nresolved temporaries:")
    for symbol in sorted(temporaries):
        definitions = assignments[symbol]
        locations = ", ".join(str(item.line_number) for item in definitions)
        print(f"  {symbol}: lines {locations}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
