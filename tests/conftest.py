"""Shared pytest configuration for Caliper's test suite.

Phase 1 needs a master key before any secret store can be opened. The suite
uses one fixed, non-secret test key and a throwaway data directory so tests
never touch ``/data`` or the developer's real secrets.
"""

import base64
import os
import tempfile

_TEST_ROOT = tempfile.mkdtemp(prefix="caliper-tests-")
os.environ.setdefault("CALIPER_MASTER_KEY", base64.urlsafe_b64encode(b"\x01" * 32).decode("ascii"))
os.environ.setdefault("CALIPER_DATA_DIR", os.path.join(_TEST_ROOT, "data"))
os.environ.setdefault("CALIPER_RUNTIME_DIR", os.path.join(_TEST_ROOT, "run"))
