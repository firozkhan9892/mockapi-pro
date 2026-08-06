"""File storage helpers for generated images.

Images are stored under storage/images/ with unique, server-generated
filenames. All path resolution is validated to prevent traversal.
"""

import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_image_storage_dir = os.environ.get("IMAGE_STORAGE_DIR", "").strip()
STORAGE_DIR = Path(_image_storage_dir) if _image_storage_dir else BASE_DIR / "storage" / "images"
RELATIVE_PREFIX = Path("storage") / "images"


def init_storage():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def make_filename(width, height):
    return f"{uuid.uuid4().hex}_{width}x{height}.png"


def save(data, filename):
    _validate_filename(filename)
    init_storage()
    (STORAGE_DIR / filename).write_bytes(data)
    return (RELATIVE_PREFIX / filename).as_posix()


def resolve(filename):
    _validate_filename(filename)
    return STORAGE_DIR / filename


def remove(filename):
    try:
        resolve(filename).unlink()
    except (OSError, ValueError):
        pass


def _validate_filename(filename):
    if not filename or Path(filename).name != filename:
        raise ValueError("Invalid storage path")
    target = (STORAGE_DIR / filename).resolve()
    root = STORAGE_DIR.resolve()
    if target == root or root not in target.parents:
        raise ValueError("Invalid storage path")
