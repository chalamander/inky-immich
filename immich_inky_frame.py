import gc
import time
from urllib import urequest

import jpegdec
from inky_frame import BLACK, RED, WHITE
from ujson import load

gc.collect()

graphics = None
WIDTH = 800
HEIGHT = 480

# Change the server address to where the inky-immich service is running.
IMAGE_URL = "http://your-service-host:18765/image.jpg"
FILENAME = "immich-image.jpg"

# Length of time between updates in minutes.
# Frequent updates will reduce battery life!
UPDATE_INTERVAL = 240

RETRY_DELAY_SECONDS = 10
MAX_FETCH_ATTEMPTS = 20

image_error = None
image_metadata = ""


def _metadata_url():
    image_path, separator, query = IMAGE_URL.partition("?")
    metadata_path = image_path.rsplit("/", 1)[0] + "/image-metadata.json"
    if separator:
        return metadata_path + "?" + query
    return metadata_path


def _show_metadata():
    query = IMAGE_URL.split("?", 1)[1] if "?" in IMAGE_URL else ""
    for parameter in query.split("&"):
        key, separator, value = parameter.partition("=")
        if key == "showMetadata" and separator:
            return value.lower() in ("1", "true", "yes", "on")
    return False


def _image_url_for_asset(metadata):
    image_path = IMAGE_URL.split("?", 1)[0]
    return "{}?assetId={}&orientation={}".format(
        image_path,
        metadata["assetId"],
        metadata.get("orientation", "both"),
    )


def update():
    global image_error, image_metadata

    image_error = None
    image_metadata = ""
    for attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            metadata = None
            if _show_metadata():
                metadata_response = urequest.urlopen(_metadata_url())
                try:
                    metadata = load(metadata_response)
                finally:
                    metadata_response.close()
                image_url = _image_url_for_asset(metadata)
            else:
                image_url = IMAGE_URL

            response = urequest.urlopen(image_url)
            try:
                downloaded = 0
                data = bytearray(1024)
                with open(FILENAME, "wb") as image_file:
                    while True:
                        bytes_read = response.readinto(data)
                        if not bytes_read:
                            break
                        if downloaded == 0 and (
                            bytes_read < 2 or data[0] != 0xFF or data[1] != 0xD8
                        ):
                            raise ValueError("Image service did not return a JPEG")
                        image_file.write(data[:bytes_read])
                        downloaded += bytes_read

                if downloaded == 0:
                    raise ValueError("Image service returned an empty image")
            finally:
                response.close()
            if metadata:
                image_metadata = ", ".join(
                    value for value in (
                        metadata.get("date"),
                        metadata.get("location"),
                        metadata.get("album"),
                    )
                    if value
                )
        except (OSError, ValueError) as error:
            print(error)
            if attempt == MAX_FETCH_ATTEMPTS - 1:
                image_error = "Unable to fetch image"
            else:
                time.sleep(RETRY_DELAY_SECONDS)
        else:
            break
        gc.collect()

    gc.collect()


def draw_error(message):
    graphics.set_pen(RED)
    graphics.rectangle(0, (HEIGHT // 2) - 20, WIDTH, 40)
    graphics.set_pen(BLACK)
    graphics.text(message, 5, (HEIGHT // 2) - 10, WIDTH - 10, 2)


def draw():
    graphics.set_pen(WHITE)
    graphics.clear()

    if image_error:
        draw_error(image_error)
    else:
        try:
            jpeg = jpegdec.JPEG(graphics)
            jpeg.open_file(FILENAME)
            jpeg.decode(dither=False)
        except (OSError, RuntimeError) as error:
            print(error)
            draw_error("Unable to display image")
        else:
            if image_metadata:
                graphics.set_pen(WHITE)
                graphics.rectangle(0, HEIGHT - 25, WIDTH, 25)
                graphics.set_pen(BLACK)
                graphics.text(image_metadata, 5, HEIGHT - 20, WIDTH - 10, 2)

    gc.collect()
    graphics.update()
