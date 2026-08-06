"""Request validation for the Image API."""

SUPPORTED_MODELS = ("flux-schnell", "stable-diffusion-xl", "dall-e-3")
DEFAULT_MODEL = "flux-schnell"
DEFAULT_SIZE = 1024
MIN_SIZE = 64
MAX_SIZE = 1024
MAX_PROMPT_LENGTH = 2000


def validate_generate_payload(data):
    """Validate a generate request body.

    Returns (ok, normalized_payload, error_message).
    """
    errors = []
    if not isinstance(data, dict):
        return False, None, "Request body must be a JSON object"

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        errors.append("prompt is required")
    elif len(prompt) > MAX_PROMPT_LENGTH:
        errors.append(f"prompt must be at most {MAX_PROMPT_LENGTH} characters")

    negative_prompt = (data.get("negative_prompt") or "").strip()
    if len(negative_prompt) > MAX_PROMPT_LENGTH:
        errors.append(f"negative_prompt must be at most {MAX_PROMPT_LENGTH} characters")

    model = (data.get("model") or DEFAULT_MODEL).strip()
    if model not in SUPPORTED_MODELS:
        errors.append(f"model must be one of: {', '.join(SUPPORTED_MODELS)}")

    width = data.get("width", DEFAULT_SIZE)
    if not isinstance(width, int) or isinstance(width, bool):
        errors.append("width must be an integer")
    elif not (MIN_SIZE <= width <= MAX_SIZE):
        errors.append(f"width must be between {MIN_SIZE} and {MAX_SIZE}")

    height = data.get("height", DEFAULT_SIZE)
    if not isinstance(height, int) or isinstance(height, bool):
        errors.append("height must be an integer")
    elif not (MIN_SIZE <= height <= MAX_SIZE):
        errors.append(f"height must be between {MIN_SIZE} and {MAX_SIZE}")

    if errors:
        return False, None, "; ".join(errors)

    return True, {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "width": width,
        "height": height,
    }, None
