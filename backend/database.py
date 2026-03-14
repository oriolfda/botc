# backend/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Directori base del backend
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "botc.db")

# Assegurem que el directori existeix
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)

# Connexió SQLite (única i comuna per tot)
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Motor SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Sessió per defecte
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base per a models ORM
Base = declarative_base()

# Dependència per FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
