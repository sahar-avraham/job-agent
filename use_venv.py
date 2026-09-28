"""Restart the running script under the project's own Python when started with another.

The scripts that talk to Claude or write Word files need packages that live only in
.venv, and a fresh terminal's `python` is the system one, which lacks them. Importing
this before anything else makes `python run.py` and `.venv\\Scripts\\python run.py`
behave the same, from any terminal.
"""

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# The marker stops a loop if the venv's own Python somehow fails the check below.
if (
    PYTHON.is_file()
    and pathlib.Path(sys.prefix).resolve() != VENV.resolve()
    and not os.environ.get("JOB_AGENT_RELAUNCHED")
):
    environment = dict(os.environ, JOB_AGENT_RELAUNCHED="1")
    try:
        code = subprocess.call([str(PYTHON), *sys.orig_argv[1:]], env=environment)
    except KeyboardInterrupt:
        code = 130  # Ctrl+C already reached the child, which stops on its own
    sys.exit(code)
