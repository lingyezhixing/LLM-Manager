import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Put repo root on the path so `import core.probes` etc. work from anywhere.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# Pin cwd so relative config/db paths resolve.
os.chdir(REPO_ROOT)