"""Source versions for derived constant tables, independent of shader libraries.

The renderer accepts the existing chain / inputs prefix. Build reuse also checks
the analysis suffix so changing an analyzer cannot keep an older table alive.
"""
import hashlib
from pathlib import Path


SOURCES = {
    'c1_guard.py': ('dmsh_analysis_stamp.py', 'dmsh_analysis.py', 'c1_guard.py'),
    'cb_extent.py': ('dmsh_analysis_stamp.py', 'dmsh_analysis.py', 'c1_guard.py', 'cb_extent.py'),
}


def table_version(tool, tools_dir=None):
    """Hash the analyzer and its analysis dependencies by content, not mtime."""
    root = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in SOURCES[tool]:
        digest.update(name.encode() + b'\0' + (root / name).read_bytes() + b'\0')
    return digest.hexdigest()
