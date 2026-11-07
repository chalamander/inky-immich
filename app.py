import io
import os
import random
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

app = Flask(__name__)

WIDTH = 800
HEIGHT = 480
REQUEST_TIMEOUT = (5, 20)
JPEG_QUALITY = 100
SATURATION_FACTOR = 1.3
BRIGHTNESS_FACTOR = 1.1
GAMMA = 0.8
GAMMA_LUT = [round(255 * (value / 255) ** GAMMA) for value in range(256)] * 3
ORIENTATIONS = {"landscape", "portrait", "both"}


class ServiceError(Exception):
    pass


class ConfigurationError(ServiceError):
    pass


def process_image(image_bytes, orientation="both"):
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGB")
            if orientation == "portrait":
                oriented = oriented.transpose(Image.Transpose.ROTATE_270)
            fitted = ImageOps.fit(
                oriented,
                (WIDTH, HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            saturated = ImageEnhance.Color(fitted).enhance(SATURATION_FACTOR)
            brightened = ImageEnhance.Brightness(saturated).enhance(BRIGHTNESS_FACTOR)
            enhanced = brightened.point(GAMMA_LUT)
        output = io.BytesIO()
        enhanced.save(
            output,
            format="JPEG",
            quality=JPEG_QUALITY,
            subsampling=0,
        )
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ServiceError("Immich image could not be processed.") from error
    return output.getvalue()


def _matches_orientation(image_bytes, orientation):
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            oriented = ImageOps.exif_transpose(source)
            if orientation == "landscape":
                return oriented.width > oriented.height
            return oriented.height > oriented.width
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ServiceError("Immich image could not be processed.") from error


def _configuration():
    immich_url = os.environ.get("IMMICH_URL", "").rstrip("/")
    api_key = os.environ.get("IMMICH_API_KEY", "")
    if not immich_url or not api_key:
        raise ConfigurationError("IMMICH_URL and IMMICH_API_KEY must both be configured.")
    api_base = immich_url if immich_url.endswith("/api") else immich_url + "/api"
    return api_base, api_key


def _response_json(response, operation):
    try:
        response.raise_for_status()
    except requests.RequestException as error:
        status = getattr(response, "status_code", None)
        detail = f" (HTTP {status})" if status is not None else ""
        raise ServiceError(f"Immich {operation} failed{detail}.") from error
    try:
        return response.json()
    except ValueError as error:
        raise ServiceError(f"Immich {operation} returned invalid JSON.") from error


def fetch_random_thumbnail(orientation="both", people=None):
    api_base, api_key = _configuration()
    headers = {"x-api-key": api_key}
    search_filter: dict[str, object] = {"type": {"eq": "IMAGE"}}
    if people:
        search_filter["personIds"] = {"any": people}
    try:
        search_response = requests.post(
            f"{api_base}/search/random",
            json={"size": 20, "filter": search_filter},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        raise ServiceError("Could not connect to Immich for random image search.") from error

    result = _response_json(search_response, "random image search")
    if isinstance(result, dict):
        assets = result.get("assets", [])
        if isinstance(assets, dict):
            assets = assets.get("items", [])
    else:
        assets = result
    if not isinstance(assets, list):
        raise ServiceError("Immich random image search returned an unexpected response.")

    candidates = [
        asset for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("id"), str) and asset["id"]
    ]
    if not candidates:
        raise ServiceError("Immich returned no images.")

    if orientation == "both":
        candidates = [random.choice(candidates)]
    else:
        random.shuffle(candidates)

    for candidate in candidates:
        asset_id = quote(candidate["id"], safe="")
        try:
            thumbnail_response = requests.get(
                f"{api_base}/assets/{asset_id}/thumbnail",
                params={"size": "preview"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as error:
            raise ServiceError("Could not download an Immich image thumbnail.") from error
        try:
            thumbnail_response.raise_for_status()
        except requests.RequestException as error:
            status = getattr(thumbnail_response, "status_code", None)
            detail = f" (HTTP {status})" if status is not None else ""
            raise ServiceError(f"Immich thumbnail download failed{detail}.") from error

        if not thumbnail_response.content:
            raise ServiceError("Immich returned an empty image thumbnail.")
        if orientation == "both" or _matches_orientation(
            thumbnail_response.content,
            orientation,
        ):
            return thumbnail_response.content

    raise ServiceError(f"Immich returned no {orientation} images.")


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/image.jpg")
def image():
    orientation = request.args.get("orientation", "both")
    if orientation not in ORIENTATIONS:
        return jsonify(error="orientation must be landscape, portrait, or both."), 400
    people = [
        person_id.strip()
        for value in request.args.getlist("people")
        for person_id in value.split(",")
        if person_id.strip()
    ]

    try:
        jpeg_bytes = process_image(
            fetch_random_thumbnail(orientation, people),
            orientation,
        )
    except ServiceError as error:
        app.logger.warning("Image request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code

    return Response(jpeg_bytes, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
