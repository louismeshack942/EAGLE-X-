"""Database bootstrap: create tables and seed the known Deriv synthetic symbols.

Markets list reflects Deriv's PUBLIC symbol catalogue; availability is confirmed at
runtime via the live active_symbols feed, not invented.
"""

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models.models import Market

SEEDED_SYMBOLS = [
    ("R_10", "Volatility 10 (1s) Index", "volatility"),
    ("R_25", "Volatility 25 (1s) Index", "volatility"),
    ("R_50", "Volatility 50 (1s) Index", "volatility"),
    ("R_75", "Volatility 75 (1s) Index", "volatility"),
    ("R_100", "Volatility 100 (1s) Index", "volatility"),
    ("RDBULL", "Volatility 50 (1s) Index (rise)", "volatility"),
    ("RDBEAR", "Volatility 100 (1s) Index (fall)", "volatility"),
]


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = {m.symbol for m in db.query(Market).all()}
        for symbol, name, cat in SEEDED_SYMBOLS:
            if symbol not in existing:
                db.add(Market(symbol=symbol, display_name=name, category=cat, is_active=False))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    print(f"Initializing DB: {settings.database_url}")
    init_db()
    print("DB initialized.")