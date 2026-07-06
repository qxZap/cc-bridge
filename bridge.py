#!/usr/bin/env python3
"""Thin shim so `python bridge.py` still works from a clone without installing.
The real code lives in the cc_bridge package (so `uv tool install` can expose a
`cc-bridge` command). See README."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc_bridge.server import main

if __name__ == "__main__":
    main()
