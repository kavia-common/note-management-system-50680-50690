import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy import create_engine, text

# PUBLIC_INTERFACE
def get_database_url() -> str:
    """Return the database URL from environment variables.

    Priority order:
    - POSTGRES_URL (complete URL)
    - Constructed from POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT

    Note: Environment variables must be set by the orchestrator in .env; do not hardcode secrets.
    """
    url = os.getenv("POSTGRES_URL")
    if url:
        return url
    user = os.getenv("POSTGRES_USER", "appuser")
    password = os.getenv("POSTGRES_PASSWORD", "dbuser123")
    db = os.getenv("POSTGRES_DB", "myapp")
    port = os.getenv("POSTGRES_PORT", "5000")
    host = os.getenv("POSTGRES_HOST", "localhost")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


# SQLAlchemy setup (sync engine for simplicity and broader compatibility)
DATABASE_URL = get_database_url()
engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


class NoteORM(Base):
    """SQLAlchemy ORM model for notes table."""
    __tablename__ = "notes"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# PUBLIC_INTERFACE
class NoteCreate(BaseModel):
    """Payload model for creating a note."""
    title: str = Field(..., description="Title of the note", min_length=1, max_length=255)
    content: str = Field("", description="Content of the note")


# PUBLIC_INTERFACE
class NoteUpdate(BaseModel):
    """Payload model for updating a note."""
    title: Optional[str] = Field(None, description="Updated title", min_length=1, max_length=255)
    content: Optional[str] = Field(None, description="Updated content")


# PUBLIC_INTERFACE
class Note(BaseModel):
    """Response model for a note."""
    id: uuid.UUID
    title: str
    content: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def init_db():
    """Initialize database: ensure uuid extension and create notes table if missing."""
    # Ensure uuid-ossp extension exists (not strictly required when client generates UUIDs)
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""))
        except Exception:
            # Safe to ignore if extension create not permitted
            pass
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Dependency that provides a DB session and ensures proper close/rollback."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


openapi_tags = [
    {"name": "Health", "description": "Service health and meta."},
    {"name": "Notes", "description": "CRUD operations for notes."},
]

app = FastAPI(
    title="Notes API",
    description="FastAPI backend for Notes with PostgreSQL storage. Provides CRUD endpoints for notes.",
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS: allow frontend at port 3000
frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin, "*"],  # permissive to ease preview links
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Initialize DB on app startup."""
    init_db()


# PUBLIC_INTERFACE
@app.get("/", tags=["Health"], summary="Health Check")
def health_check():
    """Health check endpoint.

    Returns a simple status indicating the service is running.
    """
    return {"message": "Healthy"}


# PUBLIC_INTERFACE
@app.get(
    "/notes",
    response_model=List[Note],
    tags=["Notes"],
    summary="List notes",
    description="Retrieve all notes ordered by updated_at DESC.",
    responses={200: {"description": "List of notes"}},
)
def list_notes(db: Session = Depends(get_db)):
    """List all notes."""
    notes = db.query(NoteORM).order_by(NoteORM.updated_at.desc()).all()
    return notes


# PUBLIC_INTERFACE
@app.get(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Get note by ID",
    description="Retrieve a single note by its UUID.",
    responses={404: {"description": "Note not found"}},
)
def get_note(note_id: uuid.UUID, db: Session = Depends(get_db)):
    """Get a note by ID."""
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


# PUBLIC_INTERFACE
@app.post(
    "/notes",
    response_model=Note,
    status_code=status.HTTP_201_CREATED,
    tags=["Notes"],
    summary="Create note",
    description="Create a new note with title and optional content.",
)
def create_note(payload: NoteCreate, db: Session = Depends(get_db)):
    """Create a new note."""
    now = datetime.utcnow()
    note = NoteORM(
        title=payload.title,
        content=payload.content or "",
        created_at=now,
        updated_at=now,
    )
    db.add(note)
    db.flush()  # assign ID
    db.refresh(note)
    return note


# PUBLIC_INTERFACE
@app.put(
    "/notes/{note_id}",
    response_model=Note,
    tags=["Notes"],
    summary="Update note",
    description="Update a note's title and/or content by ID.",
    responses={404: {"description": "Note not found"}},
)
def update_note(note_id: uuid.UUID, payload: NoteUpdate, db: Session = Depends(get_db)):
    """Update an existing note."""
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if payload.title is not None:
        note.title = payload.title
    if payload.content is not None:
        note.content = payload.content
    note.updated_at = datetime.utcnow()
    db.add(note)
    db.flush()
    db.refresh(note)
    return note


# PUBLIC_INTERFACE
@app.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Notes"],
    summary="Delete note",
    description="Delete a note by ID.",
    responses={404: {"description": "Note not found"}, 204: {"description": "Deleted"}},
)
def delete_note(note_id: uuid.UUID, db: Session = Depends(get_db)):
    """Delete a note by ID."""
    note = db.get(NoteORM, note_id)
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    db.delete(note)
    return None
