import sys
from config.config import MISSING

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    MISSING.append("rich")

if MISSING:
    print(f"[error] Missing packages: {', '.join(MISSING)}")
    print(f"  pip install {' '.join(MISSING)}")
    sys.exit(1)

console = Console()
