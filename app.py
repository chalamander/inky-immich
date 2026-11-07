import io
import os
import random
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify
from PIL import Image, ImageOps, UnidentifiedImageError

app = Flask(__name__)

WIDTH = 800
HEIGHT = 480
PALETTE = ("#000000", "#FFFFFF", "#A02020", "#F0E050", "#608050", "#5080B8")
REQUEST_TIMEOUT = (5, 20)
JPEG_QUALITY = 95


class ServiceError(Exception):
    pass


class ConfigurationError(ServiceError):
    pass


def _palette_image():
    colors = [
        int(PALETTE[index][offset : offset + 2], 16)
        for index in range(len(PALETTE))
        for offset in (1, 3, 5)
    ]
    palette = Image.new("P", (1, 1))
    palette.putpalette(colors + [0] * (768 - len(colors)))
    return palette


def process_image(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGB")
            fitted = ImageOps.fit(
                oriented,
                (WIDTH, HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        quantized = fitted.quantize(
            palette=_palette_image(),
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        output = io.BytesIO()
        quantized.convert("RGB").save(
            output,
            format="JPEG",
            quality=JPEG_QUALITY,
            subsampling=0,
        )
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ServiceError("Immich image could not be processed.") from error
    return output.getvalue()


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


def fetch_random_thumbnail():
    api_base, api_key = _configuration()
    headers = {"x-api-key": api_key}
    try:
        search_response = requests.post(
            f"{api_base}/search/random",
            json={"size": 20, "filter": {"type": {"eq": "IMAGE"}}},
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

    asset_id = quote(random.choice(candidates)["id"], safe="")
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
    return thumbnail_response.content


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/image.jpg")
def image():
    try:
        jpeg_bytes = process_image(fetch_random_thumbnail())
    except ServiceError as error:
        app.logger.warning("Image request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code

    return Response(jpeg_bytes, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
