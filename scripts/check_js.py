"""Syntax-check the shared script and the terminal's inline application."""
from pathlib import Path
import re
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
subprocess.run(['node','--check',str(ROOT/'script.js')],check=True)
scripts=re.findall(r'<script[^>]*>([\s\S]*?)</script>',(ROOT/'terminal.html').read_text())
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'terminal.js';path.write_text(scripts[-1])
    subprocess.run(['node','--check',str(path)],check=True)
