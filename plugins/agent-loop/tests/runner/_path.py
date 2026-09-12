"""Put the plugin root on sys.path so `import runner.x` works under unittest discover.

`python3 -m unittest discover -s tests/runner` puts tests/runner on sys.path[0],
not the plugin root, so every test module starts with `import _path  # noqa: F401`.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
