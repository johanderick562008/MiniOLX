"""Database connection: engine, session factory, and the ORM base class."""
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# URL.create escapes special characters in the password (e.g. @ or #),
# which a hand-built "mysql+pymysql://user:pass@host/db" string would break on.
DATABASE_URL = URL.create(
    drivername="mysql+pymysql",
    username=settings.DB_USER,
    password=settings.DB_PASSWORD,
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    database=settings.DB_NAME,
    query={"charset": "utf8mb4"},
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # test a pooled connection before using it
    pool_recycle=3600,    # replace connections before MySQL's idle timeout kills them
    echo=settings.DB_ECHO,  # set DB_ECHO=true in .env to print every SQL query
    # Every connection works in UTC, so CURRENT_TIMESTAMP in MySQL and
    # datetime values from Python always mean the same thing.
    connect_args={"init_command": "SET time_zone = '+00:00'"},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """All ORM models (User, Product, ...) inherit from this."""


def get_db():
    """FastAPI dependency: one session per request, always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
