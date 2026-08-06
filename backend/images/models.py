"""Database access and image metadata model for the Image API."""

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_database_path = os.environ.get("DATABASE_PATH", "").strip()
DB_NAME = Path(_database_path) if _database_path else BASE_DIR / "mockapi.db"

STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

CREATE_IMAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_images (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT DEFAULT '',
    model TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    filename TEXT,
    filepath TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_image_table():
    conn = get_db()
    try:
        conn.execute(CREATE_IMAGES_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


class ImageRecord:
    """Metadata for a single generated image."""

    def __init__(self, id, user_id, prompt, negative_prompt, model, width, height,
                 status, filename, filepath, created_at):
        self.id = id
        self.user_id = user_id
        self.prompt = prompt
        self.negative_prompt = negative_prompt
        self.model = model
        self.width = width
        self.height = height
        self.status = status
        self.filename = filename
        self.filepath = filepath
        self.created_at = created_at

    @classmethod
    def from_row(cls, row):
        return cls(
            row["id"], row["user_id"], row["prompt"], row["negative_prompt"],
            row["model"], row["width"], row["height"], row["status"],
            row["filename"], row["filepath"], row["created_at"],
        )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "status": self.status,
            "filename": self.filename,
            "filepath": self.filepath,
            "created_at": self.created_at,
            "url": f"/api/v1/images/{self.id}",
        }


def create_image(image_id, user_id, prompt, negative_prompt, model, width, height, status):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO ai_images (id, user_id, prompt, negative_prompt, model, width, height, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (image_id, user_id, prompt, negative_prompt, model, width, height, status),
        )
        conn.commit()
    finally:
        conn.close()
    return get_image(image_id)


def update_image(image_id, status, filename, filepath):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE ai_images SET status=?, filename=?, filepath=? WHERE id=?",
            (status, filename, filepath, image_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_image(image_id)


def get_image(image_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM ai_images WHERE id=?", (image_id,)).fetchone()
    finally:
        conn.close()
    return ImageRecord.from_row(row) if row else None


def list_images(user_id, limit=50, offset=0):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM ai_images WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [ImageRecord.from_row(r) for r in rows]


def delete_image(image_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM ai_images WHERE id=?", (image_id,))
        conn.commit()
    finally:
        conn.close()
