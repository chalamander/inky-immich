import io
import os
import random
from datetime import date
from uuid import UUID
from urllib.parse import quote

import regex
import requests
from flask import Flask, Response, jsonify, request
from PIL import Image, ImageEnhance, ImageOps, ImageStat, UnidentifiedImageError

app = Flask(__name__)

WIDTH = 800
HEIGHT = 480
REQUEST_TIMEOUT = (5, 20)
JPEG_QUALITY = 90
SATURATION_FACTOR = 2.5
GAMMA = 0.68
CONTRAST_FACTOR = 1.1
MINIMUM_AVERAGE_BRIGHTNESS = 80
GAMMA_LUT = [round(255 * (value / 255) ** GAMMA) for value in range(256)]
ORIENTATIONS = {"landscape", "portrait", "both"}
DEFAULT_ORIENTATION = "landscape"
DEFAULT_EXCLUDE_DARKER_IMAGES = True
EMOJI_BASE_PATTERN = (
    r"(?:\p{Emoji_Presentation}\uFE0F?"
    r"|\p{Extended_Pictographic}\uFE0F)"
)
EMOJI_PATTERN = regex.compile(
    rf"(?:{EMOJI_BASE_PATTERN}(?:\p{{Emoji_Modifier}})?"
    rf"(?:\u200D{EMOJI_BASE_PATTERN}(?:\p{{Emoji_Modifier}})?)*"
    r"(?:[\U000E0020-\U000E007E]*\U000E007F)?)"
    r"|(?:\p{Regional_Indicator}{2})"
    r"|(?:[#*0-9]\uFE0F?\u20E3)"
)


class ServiceError(Exception):
    pass


class ConfigurationError(ServiceError):
    pass


def _remove_emojis(text):
    return " ".join(EMOJI_PATTERN.sub("", text).split())


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
            luminance, chroma_blue, chroma_red = fitted.convert("YCbCr").split()
            lifted_luminance = luminance.point(GAMMA_LUT)
            brightened = Image.merge(
                "YCbCr",
                (lifted_luminance, chroma_blue, chroma_red),
            ).convert("RGB")
            high_contrast = ImageEnhance.Contrast(brightened).enhance(
                CONTRAST_FACTOR
            )
            enhanced = ImageEnhance.Color(high_contrast).enhance(SATURATION_FACTOR)
        output = io.BytesIO()
        enhanced.save(
            output,
            format="JPEG",
            quality=JPEG_QUALITY,
            subsampling=2,
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


def _matches_minimum_brightness(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            grayscale = ImageOps.exif_transpose(source).convert("L")
            return ImageStat.Stat(grayscale).mean[0] >= MINIMUM_AVERAGE_BRIGHTNESS
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ServiceError("Immich image could not be processed.") from error


def _boolean_query_or_environment(
    name,
    environment_name,
    default=DEFAULT_EXCLUDE_DARKER_IMAGES,
):
    value = _query_or_environment(name, environment_name)
    return _parse_boolean_value(name, value, default)


def _parse_boolean_value(name, value, default=False):
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean.")


def _query_or_environment(query_name, environment_name, default=None):
    if query_name in request.args:
        return request.args.get(query_name, "")
    value = os.environ.get(environment_name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _list_query_or_environment(query_name, environment_name):
    if query_name in request.args:
        values = request.args.getlist(query_name)
    else:
        values = [os.environ.get(environment_name, "")]
    return [
        item.strip()
        for value in values
        for item in value.split(",")
        if item.strip()
    ]


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


def _is_tag_id(value):
    try:
        tag_id = UUID(value)
    except ValueError:
        return False
    return tag_id.version == 4 and str(tag_id) == value.lower()


def _resolve_tag_filters(include_tags, exclude_tags, api_base, headers):
    requested_tags = include_tags + exclude_tags
    names = [tag for tag in requested_tags if not _is_tag_id(tag)]
    if not names:
        return include_tags, exclude_tags

    try:
        tags_response = requests.get(
            f"{api_base}/tags",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as error:
        raise ServiceError("Could not connect to Immich for tag lookup.") from error

    tags = _response_json(tags_response, "tag lookup")
    if not isinstance(tags, list):
        raise ServiceError("Immich tag lookup returned an unexpected response.")

    def resolve(tag_value):
        if _is_tag_id(tag_value):
            return tag_value

        full_path_matches = {
            tag["id"]
            for tag in tags
            if isinstance(tag, dict)
            and tag.get("value") == tag_value
            and isinstance(tag.get("id"), str)
        }
        matches = full_path_matches or {
            tag["id"]
            for tag in tags
            if isinstance(tag, dict)
            and tag.get("name") == tag_value
            and isinstance(tag.get("id"), str)
        }
        if not matches:
            raise ServiceError(
                f"Immich tag '{tag_value}' was not found. Use an exact tag name or ID."
            )
        if len(matches) > 1:
            raise ServiceError(
                f"Immich tag name '{tag_value}' is ambiguous. Use its full path or ID."
            )
        return next(iter(matches))

    return (
        [resolve(tag) for tag in include_tags],
        [resolve(tag) for tag in exclude_tags],
    )


def _find_random_asset(
    orientation=DEFAULT_ORIENTATION,
    people=None,
    include_tags=None,
    exclude_tags=None,
    exclude_darker_images=DEFAULT_EXCLUDE_DARKER_IMAGES,
):
    api_base, api_key = _configuration()
    headers = {"x-api-key": api_key}
    search_filter: dict[str, object] = {"type": {"eq": "IMAGE"}}
    if people:
        search_filter["personIds"] = {"any": people}
    include_tags, exclude_tags = _resolve_tag_filters(
        include_tags or [],
        exclude_tags or [],
        api_base,
        headers,
    )
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

    if orientation == "both" and not exclude_darker_images:
        return random.choice(candidates), None, api_base, headers
    random.shuffle(candidates)

    for candidate in candidates:
        thumbnail = _download_thumbnail(api_base, headers, candidate["id"])
        if (
            (orientation == "both" or _matches_orientation(thumbnail, orientation))
            and (
                not exclude_darker_images
                or _matches_minimum_brightness(thumbnail)
            )
        ):
            return candidate, thumbnail, api_base, headers

    if exclude_darker_images and orientation == "both":
        raise ServiceError(
            "Immich returned no images meeting the minimum average brightness."
        )
    if exclude_darker_images:
        raise ServiceError(
            f"Immich returned no {orientation} images meeting the minimum average brightness."
        )
    raise ServiceError(f"Immich returned no {orientation} images.")


def fetch_random_thumbnail(
    orientation=DEFAULT_ORIENTATION,
    people=None,
    include_tags=None,
    exclude_tags=None,
    exclude_darker_images=DEFAULT_EXCLUDE_DARKER_IMAGES,
):
    asset, thumbnail, api_base, headers = _find_random_asset(
        orientation,
        people,
        include_tags,
        exclude_tags,
        exclude_darker_images,
    )
    if thumbnail is None:
        thumbnail = _download_thumbnail(api_base, headers, asset["id"])
    return thumbnail


def _image_filters():
    return {
        "people": _list_query_or_environment("people", "PEOPLE"),
        "include_tags": _list_query_or_environment("includeTags", "INCLUDE_TAGS"),
        "exclude_tags": _list_query_or_environment("excludeTags", "EXCLUDE_TAGS"),
        "exclude_darker_images": _boolean_query_or_environment(
            "excludeDarkerImages",
            "EXCLUDE_DARKER_IMAGES",
        ),
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
            return _remove_emojis(album["albumName"])
    return ""


def _format_image_date(created_at):
    if not isinstance(created_at, str):
        return ""
    try:
        image_date = date.fromisoformat(created_at[:10])
    except ValueError:
        return ""

    day = image_date.day
    suffix = "th" if 10 < day % 100 < 14 else {
        1: "st",
        2: "nd",
        3: "rd",
    }.get(day % 10, "th")
    month = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )[image_date.month - 1]
    return f"{day}{suffix} {month} {image_date.year}"


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

    return {
        "assetId": asset["id"],
        "orientation": orientation,
        "date": _format_image_date(asset.get("fileCreatedAt")),
        "location": location,
        "album": album,
    }


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/image-metadata.json")
def image_metadata():
    try:
        show_metadata = _parse_boolean_value(
            "showMetadata",
            request.args.get("showMetadata"),
        )
        orientation = _query_or_environment(
            "orientation",
            "ORIENTATION",
            DEFAULT_ORIENTATION,
        )
    except ValueError as error:
        return jsonify(error=str(error)), 400

    if orientation not in ORIENTATIONS:
        return jsonify(error="orientation must be landscape, portrait, or both."), 400

    if not show_metadata:
        return jsonify(showMetadata=False)

    try:
        filters = _image_filters()
        asset, _, api_base, headers = _find_random_asset(
            orientation,
            filters["people"],
            filters["include_tags"],
            filters["exclude_tags"],
            filters["exclude_darker_images"],
        )
        album = _fetch_asset_album(asset["id"], api_base, headers)
        metadata = _asset_metadata(asset, album, orientation)
        metadata["showMetadata"] = True
        metadata["excludeDarkerImages"] = filters["exclude_darker_images"]
        return jsonify(metadata)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except ServiceError as error:
        app.logger.warning("Image metadata request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code


@app.get("/image.jpg")
def image():
    try:
        orientation = _query_or_environment(
            "orientation",
            "ORIENTATION",
            DEFAULT_ORIENTATION,
        )
    except ValueError as error:
        return jsonify(error=str(error)), 400

    if orientation not in ORIENTATIONS:
        return jsonify(error="orientation must be landscape, portrait, or both."), 400

    try:
        filters = _image_filters()
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
            if (
                filters["exclude_darker_images"]
                and not _matches_minimum_brightness(thumbnail)
            ):
                raise ServiceError(
                    "Immich returned an image below the minimum average brightness."
                )
        else:
            thumbnail = fetch_random_thumbnail(
                orientation,
                filters["people"],
                filters["include_tags"],
                filters["exclude_tags"],
                filters["exclude_darker_images"],
            )
        jpeg_bytes = process_image(thumbnail, orientation)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except ServiceError as error:
        app.logger.warning("Image request failed: %s", error)
        status_code = 503 if isinstance(error, ConfigurationError) else 502
        return jsonify(error=str(error)), status_code

    return Response(jpeg_bytes, mimetype="image/jpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
