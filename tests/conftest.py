import os
from pathlib import Path

os.environ.setdefault(
    "COVERAGE_PROCESS_START",
    str(Path(__file__).resolve().parent.parent / ".coveragerc"),
)
