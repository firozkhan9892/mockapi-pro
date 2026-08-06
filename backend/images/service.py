"""Business logic for the Image API.

Phase 1 generates a placeholder image so the full flow (auth, database,
storage, API) can be tested. Phase 2 will swap the placeholder for a
real AI model call behind the same interface.
"""

import logging

from . import models, storage, utils
from .providers import GenerationStatus, get_provider

logger = logging.getLogger(__name__)


def generate_image(user_id, payload):
    image_id = utils.make_image_id()
    filename = storage.make_filename(payload["width"], payload["height"])

    record = models.create_image(
        image_id=image_id,
        user_id=user_id,
        prompt=payload["prompt"],
        negative_prompt=payload.get("negative_prompt", ""),
        model=payload["model"],
        width=payload["width"],
        height=payload["height"],
        status=models.STATUS_PROCESSING,
    )

    provider = get_provider(payload["model"])
    result = provider.generate(
        prompt=payload["prompt"],
        negative_prompt=payload.get("negative_prompt", ""),
        width=payload["width"],
        height=payload["height"],
        model=payload["model"],
    )

    if result.status is GenerationStatus.COMPLETED:
        filepath = storage.save(result.image_bytes, filename)
        record = models.update_image(image_id, models.STATUS_COMPLETED, filename, filepath)
        return {"success": True, "image": record.to_dict()}, 201

    # generation failed or is still processing after timeout
    try:
        storage.remove(filename)
    except Exception:
        pass
    models.update_image(image_id, models.STATUS_FAILED, None, None)
    error = result.error or "Image generation failed"
    logger.warning("Image generation failed for record %s via %s: %s",
                   image_id, getattr(provider, "name", "unknown"), error)
    return {"success": False, "error": error}, 502


def list_user_images(user_id):
    records = models.list_images(user_id)
    return {"images": [r.to_dict() for r in records], "total": len(records)}


def get_user_image(user_id, image_id):
    record = models.get_image(image_id)
    if not record or (record.user_id != user_id and not _is_admin(user_id)):
        return None
    return record


def delete_user_image(user_id, image_id):
    record = models.get_image(image_id)
    if not record or (record.user_id != user_id and not _is_admin(user_id)):
        return {"error": "Image not found"}, 404
    if record.filename:
        storage.remove(record.filename)
    models.delete_image(image_id)
    return {"success": True}, 200


def _is_admin(user_id):
    backend = utils.get_backend()
    return backend.is_admin_user(user_id)
