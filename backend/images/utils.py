"""Small helpers: ids and placeholder image generation.

The placeholder is a minimal, dependency-free PNG generated with the
standard library, so the full request lifecycle (auth, DB, storage)
can be exercised before real AI generation lands in Phase 2.
"""

import hashlib
import struct
import uuid
import zlib


def make_image_id():
    return uuid.uuid4().hex


def get_backend():
    """Return the running MockAPI app module without re-importing it.

    Under gunicorn the app is importable as ``app``; under ``python app.py``
    it runs as ``__main__``. Re-importing it from those entry points would
    execute init_db() a second time, so prefer the already-loaded module.
    A fresh import is used only as a fallback (e.g. standalone tests).
    """
    import sys

    for name in ("app", "__main__"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "image_requester") and hasattr(mod, "is_user_active"):
            return mod
    import app as backend
    return backend


def _png_chunk(tag, data):
    chunk = struct.pack(">I", len(data)) + tag + data
    return chunk + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def make_placeholder_png(width, height, prompt=""):
    """Return placeholder PNG bytes derived from the prompt."""
    digest = hashlib.sha256((prompt or "").encode("utf-8")).digest()
    color = (digest[0], digest[1], digest[2])

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(color) * width
    idat = zlib.compress(row * height, 9)

    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
