from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.services.units import format_um

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["um"] = format_um
