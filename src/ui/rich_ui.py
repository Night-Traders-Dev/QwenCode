from config.config import _MISSING

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    _MISSING.append("rich")

if _MISSING:
    print(f"[error] Missing packages: {', '.join(_MISSING)}")
    print(f"  pip install {' '.join(_MISSING)}")
    sys.exit(1)
