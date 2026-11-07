import gc
from urllib import urequest

import jpegdec
from inky_frame import BLACK, RED, WHITE
from secrets import IMMICH_API_KEY
from ujson import dumps, load

gc.collect()

graphics = None
WIDTH = None
HEIGHT = None

FILENAME = "immich-image"
API_BASE = "http://your-immich-server:2283/api"
HEADERS = {"x-api-key": IMMICH_API_KEY}

# Length of time between updates in minutes.
# Frequent updates will reduce battery life!
UPDATE_INTERVAL = 240

image_caption = "Immich photo"
thumbnail_info = "No thumbnail downloaded"


def format_caption(asset):
    parts = []

    exif = asset.get("exifInfo") or {}
    date = exif.get("dateTimeOriginal") or asset.get("fileCreatedAt")
    if date:
        parts.append(date.split("T")[0])

    location_parts = []
    for field in ("city", "state", "country"):
        value = exif.get(field)
        if value and value not in location_parts:
            location_parts.append(value)
    if location_parts:
        parts.append(", ".join(location_parts))

    albums = asset.get("albums") or []
    if albums:
        album_name = albums[0].get("albumName") or albums[0].get("name")
        if album_name:
            parts.append(album_name)

    return ", ".join(parts) if parts else "Immich photo"


def show_error(text):
    graphics.set_pen(RED)
    graphics.rectangle(0, 10, WIDTH, 35)
    graphics.set_pen(BLACK)
    graphics.text(text, 5, 16, 400, 2)


def get_tag_id(name):
    response = urequest.urlopen(API_BASE + "/tags", headers=HEADERS)
    try:
        tags = load(response)
    finally:
        response.close()

    if isinstance(tags, dict):
        if tags.get("message"):
            raise ValueError("Immich /tags error: " + str(tags["message"]))
        if tags.get("name") == name or tags.get("value") == name:
            tag_id = tags.get("id")
            if tag_id:
                return tag_id
            raise ValueError("Immich tag is missing its ID: " + name)
        for key in ("tags", "items", "data"):
            if key in tags:
                tags = tags[key]
                break
        else:
            tag_id = tags.get(name)
            if isinstance(tag_id, str):
                return tag_id
            tag_entries = []
            for value in tags.values():
                if isinstance(value, dict):
                    tag_entries.append(value)
            if tag_entries:
                tags = tag_entries
            else:
                raise ValueError("Unexpected Immich /tags response: " + str(tags))

    if isinstance(tags, dict):
        tag_id = tags.get(name)
        if isinstance(tag_id, str):
            return tag_id
        tags = [tags]

    if not isinstance(tags, list):
        raise ValueError("Unexpected Immich /tags response: " + str(tags))

    for tag in tags:
        if isinstance(tag, dict):
            if tag.get("name") == name or tag.get("value") == name:
                tag_id = tag.get("id")
                if tag_id:
                    return tag_id
                raise ValueError("Immich tag is missing its ID: " + name)
        elif tag == name:
            raise ValueError("Immich returned the tag name without its ID: " + name)
        else:
            raise ValueError("Unexpected Immich tag entry: " + str(tag))
    return None


def get_jpeg_frame_marker(filename):
    with open(filename, "rb") as image_file:
        if image_file.read(2) != b"\xff\xd8":
            return "invalid SOI"

        while True:
            byte = image_file.read(1)
            if not byte:
                return "SOF not found"
            if byte[0] != 0xff:
                continue

            marker_byte = image_file.read(1)
            while marker_byte == b"\xff":
                marker_byte = image_file.read(1)
            if not marker_byte:
                return "truncated marker"

            marker = marker_byte[0]
            if marker == 0xda:
                return "SOF not found before SOS"
            if marker in (0xd8, 0xd9, 0x01) or 0xd0 <= marker <= 0xd7:
                continue

            length_bytes = image_file.read(2)
            if len(length_bytes) != 2:
                return "truncated segment"
            segment_length = (length_bytes[0] << 8) | length_bytes[1]
            if marker in (0xc0, 0xc1, 0xc2, 0xc3):
                return "SOF 0x{:02x}".format(marker)
            if segment_length < 2:
                return "invalid segment length"
            image_file.seek(segment_length - 2, 1)


def update():
    global image_caption, thumbnail_info

    try:
        # Exclude this tag without requiring assets to have any tags.
        excluded_tag_id = get_tag_id("kiosk-skip")
        filters = {"type": {"eq": "IMAGE"}}
        if excluded_tag_id is not None:
            filters["tagIds"] = {"none": [excluded_tag_id]}

        asset = None
        # Immich random search has no orientation filter; inspect random
        # candidates and accept only assets whose dimensions are landscape.
        for _ in range(5):
            response = urequest.urlopen(
                API_BASE + "/search/random",
                data=dumps({"size": 20, "filter": filters}).encode(),
                headers=dict(HEADERS, **{"Content-Type": "application/json"}),
            )
            try:
                result = load(response)
            finally:
                response.close()

            if isinstance(result, list):
                assets = result
            elif isinstance(result, dict):
                assets = result.get("assets", [])
            else:
                raise TypeError("Unexpected response from Immich")
            if isinstance(assets, dict):
                assets = assets.get("items", [])
            if not isinstance(assets, list):
                raise TypeError("Unexpected assets response from Immich")

            for candidate in assets:
                width = candidate.get("width")
                height = candidate.get("height")
                if width is not None and height is not None and width > height:
                    asset = candidate
                    break
            if asset is not None:
                break

        if asset is None:
            raise ValueError("Immich returned no landscape images")

        asset_id = asset["id"]
        image_caption = format_caption(asset)

        # Download a preview instead of the full-resolution original to limit
        # memory and transfer requirements on the Pico.
        image_url = API_BASE + "/assets/" + asset_id + "/thumbnail?size=preview"
        response = urequest.urlopen(image_url, headers=HEADERS)

        try:
            gc.collect()
            data = bytearray(1024)
            downloaded = 0
            first_bytes = None
            last_bytes = b""
            with open(FILENAME, "wb") as image_file:
                while True:
                    bytes_read = response.readinto(data)
                    if not bytes_read:
                        break
                    if downloaded == 0 and (bytes_read < 2 or data[0] != 0xff or data[1] != 0xd8):
                        raise ValueError("Immich thumbnail response is not a JPEG")
                    if first_bytes is None:
                        first_bytes = bytes(data[:min(bytes_read, 4)])
                    last_bytes = (last_bytes + bytes(data[:bytes_read]))[-4:]
                    image_file.write(data[:bytes_read])
                    downloaded += bytes_read
            del data
            if downloaded == 0:
                raise ValueError("Immich returned an empty thumbnail")
            thumbnail_info = "bytes={}, start={}, end={}".format(
                downloaded, first_bytes, last_bytes
            )
            thumbnail_info += ", " + get_jpeg_frame_marker(FILENAME)
        finally:
            response.close()

        gc.collect()
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(error)
        image_caption = "Image unavailable"
        show_error("Unable to fetch Immich image")


def draw():
    jpeg = jpegdec.JPEG(graphics)
    gc.collect()

    graphics.set_pen(WHITE)
    graphics.clear()

    try:
        jpeg.open_file(FILENAME)
        jpeg.decode()
    except (OSError, RuntimeError) as error:
        print(error)
        print("Thumbnail:", thumbnail_info)
        graphics.set_pen(RED)
        graphics.rectangle(0, (HEIGHT // 2) - 20, WIDTH, 40)
        graphics.set_pen(BLACK)
        graphics.text("Unable to display image!", 5, (HEIGHT // 2) - 15, WIDTH, 2)
        graphics.text("Check your Immich server and API key", 5, (HEIGHT // 2) + 2, WIDTH, 2)

    graphics.set_pen(WHITE)
    graphics.rectangle(0, HEIGHT - 25, WIDTH, 25)
    graphics.set_pen(BLACK)
    graphics.text(image_caption, 5, HEIGHT - 20, WIDTH, 2)

    gc.collect()
    graphics.update()
