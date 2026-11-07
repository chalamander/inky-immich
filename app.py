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
SHADOW_THRESHOLD = 160
MAX_SHADOW_LIFT = 32
SHADOW_LUT = [
    round(
        value + MAX_SHADOW_LIFT * (1 - value / SHADOW_THRESHOLD) ** 2
    ) if value < SHADOW_THRESHOLD else value
    for value in range(256)
]
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
            luminance, chroma_blue, chroma_red = saturated.convert("YCbCr").split()
            lifted_luminance = luminance.point(SHADOW_LUT)
            enhanced = Image.merge(
                "YCbCr",
                (lifted_luminance, chroma_blue, chroma_red),
            ).convert("RGB")
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


def _download_thumbnail(api_base, headers, asset_id):
    encoded_asset_id = quote(asset_id, safe="")
    try:
        thumbnail_response = requests.get(
            f"{api_base}/assets/{encoded_asset_id}/thumbnail",
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


def _find_random_asset(
    orientation="both",
    people=None,
    include_tags=None,
    exclude_tags=None,
):
    api_base, api_key = _configuration()
    headers = {"x-api-key": api_key}
    search_filter: dict[str, object] = {"type": {"eq": "IMAGE"}}
    if people:
        search_filter["personIds"] = {"any": people}
    tag_filter: dict[str, object] = {}
    if include_tags:
        tag_filter["any"] = include_tags
    if exclude_tags:
        tag_filter["none"] = exclude_tags
    if tag_filter:
        search_filter["tagIds"] = tag_filter
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
        return random.choice(candidates), None, api_base, headers
    else:
        random.shuffle(candidates)

    for candidate in candidates:
        thumbnail = _download_thumbnail(api_base, headers, candidate["id"])
        if _matches_orientation(thumbnail, orientation):
            return candidate, thumbnail, api_base, headers

    raise ServiceError(f"Immich returned no {orientation} images.")


def fetch_random_thumbnail(
    orientation="both",
    people=None,
    include_tags=None,
    exclude_tags=None,
):
    asset, thumbnail, api_base, headers = _find_random_asset(
        orientation,
        people,
        include_tags,
        exclude_tags,
    )
    if thumbnail is None:
        thumbnail = _download_thumbnail(api_base, headers, asset["id"])
    return thumbnail


def _image_filters():
    return {
        "people": [
            person_id.strip()
            for value in request.args.getlist("people")
            for person_id in value.split(",")
            if person_id.strip()
        ],
        "include_tags": [
            tag.strip()
            for value in request.args.getlist("includeTags")
            for tag in value.split(",")
            if tag.strip()
        ],
        "exclude_tags": [
            tag.strip()
            for value in request.args.getlist("excludeTags")
            for tag in value.split(",")
            if tag.strip()
        ],
    }


def _fetch_asset_album(asset_id, api_base, headers):
    try:
        response = requests.get(
            f"{api_base}/albums",
            params={"assetId": asset_id},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        raise ServiceError("Could not connect to Immich for album lookup.") from error
    albums = _response_json(response, "album lookup")
    if not isinstance(albums, list):
        raise ServiceError("Immich album lookup returned an unexpected response.")
    for album in albums:
        if isinstance(album, dict) and isinstance(album.get("albumName"), str):
            return album["albumName"]
    return ""


def _asset_metadata(asset, album, orientation):
    exif = asset.get("exifInfo")
    if not isinstance(exif, dict):
        exif = {}
    location_parts = [
        exif.get(field).strip()
        for field in ("city", "state", "country")
        if isinstance(exif.get(field), str) and exif[field].strip()
    ]
    location = " / ".join(location_parts)
    if not location:
        latitude = exif.get("latitude")
        longitude = exif.get("longitude")
        if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
            location = f"{latitude:.4f}/{longitude:.4f}"

    created_at = asset.get("fileCreatedAt")
    image_date = created_at[:10] if isinstance(created_at, str) else ""
    return {
        "assetId": asset["id"],
        "orientation": orientation,
        "date": image_date,
        "location": location,
        "album": album,
    }


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/image-metadata.json")
def image_metadata():
    orientation = request.args.get("orientation", "both")
    if orientation not in ORIENTATIONS:
        return jsonify(error="orientation must be landscape, portrait, or both."), 400

    try:
        filters = _image_filters()
        asset, _, api_base, headers = _find_random_asset(
            orientation,
            filters["people"],
            filters["include_tags"],
            filters["exclude_tags"],
        )
        album = _fetch_asset_album(asset["id"], api_base, headers)
        return jsonify(_asset_metadata(asset, album, orientation))
    except ServiceError as error:
        app.logger.warning("Image metadata request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code


@app.get("/image.jpg")
def image():
    orientation = request.args.get("orientation", "both")
    if orientation not in ORIENTATIONS:
        return jsonify(error="orientation must be landscape, portrait, or both."), 400

    try:
        asset_id = request.args.get("assetId")
        if asset_id:
            api_base, api_key = _configuration()
            thumbnail = _download_thumbnail(
                api_base,
                {"x-api-key": api_key},
                asset_id,
            )
            if orientation != "both" and not _matches_orientation(
                thumbnail,
                orientation,
            ):
                raise ServiceError(f"Immich returned no {orientation} images.")
        else:
            filters = _image_filters()
            thumbnail = fetch_random_thumbnail(
                orientation,
                filters["people"],
                filters["include_tags"],
                filters["exclude_tags"],
            )
        jpeg_bytes = process_image(thumbnail, orientation)
    except ServiceError as error:
        app.logger.warning("Image request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code

    return Response(jpeg_bytes, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
