"""HTTP routes for the Image API.

Authentication reuses the platform's existing session / API key auth
(see app.image_requester). API-key-authenticated requests are metered
against the daily request limit; session requests are not.
"""

from flask import Blueprint, jsonify, request, send_file

from . import schemas, service, storage, utils

images_bp = Blueprint("image_api", __name__, url_prefix="/api/v1/images")


def _auth_required():
    backend = utils.get_backend()
    user_id, key_id = backend.image_requester()
    if not user_id or not backend.is_user_active(user_id):
        return None, (jsonify({"error": "Unauthorized"}), 401)
    ok, err = backend.image_usage_check(user_id, key_id)
    if not ok:
        return user_id, err
    return user_id, None


@images_bp.route("/generate", methods=["POST"])
def generate_image():
    user_id, err = _auth_required()
    if err is not None:
        return err

    data = request.get_json(silent=True) or {}
    ok, payload, error = schemas.validate_generate_payload(data)
    if not ok:
        return jsonify({"error": error}), 400

    response, code = service.generate_image(user_id, payload)
    return jsonify(response), code


@images_bp.route("", methods=["GET"])
def list_images():
    user_id, err = _auth_required()
    if err is not None:
        return err
    return jsonify(service.list_user_images(user_id))


@images_bp.route("/<image_id>", methods=["GET"])
def get_image(image_id):
    user_id, err = _auth_required()
    if err is not None:
        return err

    record = service.get_user_image(user_id, image_id)
    if record is None:
        return jsonify({"error": "Image not found"}), 404
    if not record.filename:
        return jsonify({"error": "Image file missing"}), 404
    try:
        path = storage.resolve(record.filename)
    except ValueError:
        return jsonify({"error": "Image file missing"}), 404
    if not path.exists():
        return jsonify({"error": "Image file missing"}), 404
    return send_file(path, mimetype="image/png")


@images_bp.route("/<image_id>", methods=["DELETE"])
def delete_image(image_id):
    user_id, err = _auth_required()
    if err is not None:
        return err

    payload, code = service.delete_user_image(user_id, image_id)
    return jsonify(payload), code
