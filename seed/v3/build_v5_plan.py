#!/usr/bin/env python3
"""Story-specific episode plan builder was removed from this public seed.

The original file built a locked shot plan for one in-house episode (character
names, dialogue, locations, and plot). That text is not open-sourceable.

Use `build_example_plan.py` for the public schema, job.json layout, and a
generic 10-shot demo episode with placeholder characters.
"""
from pathlib import Path
import sys

def main():
    print(
        "This stub replaced a series-specific plan builder.\n"
        "Run:  python3 v3/build_example_plan.py",
        file=sys.stderr,
    )
    sys.exit(2)

if __name__ == "__main__":
    main()
