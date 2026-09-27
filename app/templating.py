"""Jinja2 setup shared by all HTML pages."""
import re
from datetime import datetime
from decimal import Decimal

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import APP_DIR, settings
from app.icons import ICONS
from app.schemas import CATEGORY_LABELS, CONDITION_LABELS

# Two complete sets of templates share one backend:
#   premium  app/templates                     (the Stitch design, default)
#   classic  app/themes/classic/templates      (the original design)
# Switch with THEME=classic in .env and restart.
THEME_DIRS = {
    "premium": APP_DIR / "templates",
    "classic": APP_DIR / "themes" / "classic" / "templates",
}

# Jinja2Templates escapes {{ values }} in .html files automatically, so text
# a user typed (like a product name) is shown as text, never run as HTML/JS.
templates = Jinja2Templates(directory=THEME_DIRS.get(settings.THEME, THEME_DIRS["premium"]))


def format_inr(value) -> str:
    """1500 -> '₹1,500'   150000 -> '₹1,50,000'   99.5 -> '₹99.50' (Indian grouping)."""
    amount = Decimal(value).quantize(Decimal("0.01"))
    whole, paise = f"{amount:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return f"₹{whole}" if paise == "00" else f"₹{whole}.{paise}"


def time_ago(moment: datetime | None) -> str:
    """UTC datetime -> '2h ago', '3d ago', '24 Sep'."""
    if moment is None:
        return ""
    from app.services.auth_service import utcnow
    seconds = max(0, (utcnow() - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d ago"
    return moment.strftime("%d %b")


def icon(name: str, size: int = 20, cls: str = "") -> Markup:
    """Inline SVG icon: {{ icon("search") }}. Decorative, so hidden from screen readers."""
    path = ICONS[name]
    return Markup(
        f'<svg class="icon {cls}" width="{size}" height="{size}" viewBox="0 -960 960 960" '
        f'fill="currentColor" aria-hidden="true" focusable="false"><path d="{path}"/></svg>'
    )


templates.env.filters["inr"] = format_inr
templates.env.filters["ago"] = time_ago
templates.env.globals["icon"] = icon
templates.env.globals["CATEGORY_LABELS"] = CATEGORY_LABELS
templates.env.globals["CONDITION_LABELS"] = CONDITION_LABELS
