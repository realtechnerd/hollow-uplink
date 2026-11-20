"""Tests: python -m unittest   (from the repo root; no broker, keys or network needed)."""

import logging

# The code under test logs every rejected message and flagged sensor; in tests that's expected noise.
logging.disable(logging.CRITICAL)
