import os
from pathlib import Path

# Enable coverage tracking in subprocesses spawned by tests.
# Requires COVERAGE_PROCESS_START to point at .coveragerc and a
# sitecustomize.py on PYTHONPATH that calls coverage.process_startup().
os.environ.setdefault(
    "COVERAGE_PROCESS_START",
    str(Path(__file__).resolve().parent / ".coveragerc"),
)
