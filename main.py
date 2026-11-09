import gc
import time
from urllib import urequest

import inky_helper as ih
import jpegdec
import network
from inky_frame import BLACK, RED, WHITE
from picographics import PicoGraphics, DISPLAY_INKY_FRAME_SPECTRA_7 as DISPLAY
from secrets import HOST_IP, HOST_PORT, WIFI_PASSWORD, WIFI_SSID
from ujson import load

gc.collect()

graphics = None
WIDTH = 800
HEIGHT = 480

IMAGE_URL = "http://{}:{}/image.jpg?showMetadata=true".format(HOST_IP, HOST_PORT)
FILENAME = "immich-image.jpg"

UPDATE_INTERVAL = 240
RETRY_DELAY_SECONDS = 10
MAX_FETCH_ATTEMPTS = 200
WIFI_CONNECT_TIMEOUT_SECONDS = 20
WIFI_RETRY_DELAY_SECONDS = 5

wlan = network.WLAN(network.STA_IF)
image_error = None
image_metadata = ""


def connect_wifi(force=False):
    if force:
        wlan.disconnect()
    wlan.active(True)

    while not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except OSError as error:
            print("Wi-Fi connection failed: {}".format(error))

        for _ in range(WIFI_CONNECT_TIMEOUT_SECONDS):
            if wlan.isconnected():
                print("Wi-Fi connected: {}".format(wlan.ifconfig()))
                return
            time.sleep(1)

        print("Wi-Fi connection timed out; retrying.")
        wlan.disconnect()
        time.sleep(WIFI_RETRY_DELAY_SECONDS)


def _urlopen(url):
    try:
        return urequest.urlopen(url)
    except OSError:
        connect_wifi(force=True)
        raise


def _metadata_url():
    image_path, separator, query = IMAGE_URL.partition("?")
    metadata_path = image_path.rsplit("/", 1)[0] + "/image-metadata.json"
    if separator:
        return metadata_path + "?" + query
    return metadata_path


def _image_url_for_asset(metadata):
    image_path = IMAGE_URL.split("?", 1)[0]
    return "{}?assetId={}&orientation={}&excludeDarkerImages={}".format(
        image_path,
        metadata["assetId"],
        metadata.get("orientation", "both"),
        "true" if metadata.get("excludeDarkerImages") else "false",
    )


def update():
    global image_error, image_metadata

    image_error = None
    image_metadata = ""
    for attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            metadata_response = _urlopen(_metadata_url())
            try:
                metadata = load(metadata_response)
            finally:
                metadata_response.close()
            if metadata.get("showMetadata"):
                image_url = _image_url_for_asset(metadata)
            else:
                image_url = IMAGE_URL

            response = _urlopen(image_url)
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
                            response_start = bytes(data[:min(bytes_read, 160)])
                            raise ValueError(
                                "Image service did not return a JPEG: {}".format(
                                    response_start
                                )
                            )
                        image_file.write(data[:bytes_read])
                        downloaded += bytes_read

                if downloaded == 0:
                    raise ValueError("Image service returned an empty image")
            finally:
                response.close()
            if metadata.get("showMetadata"):
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
    graphics.text(message, 5, (HEIGHT // 2) - 10, scale=2)


def _truncate_text(text, max_width, scale):
    if graphics.measure_text(text, scale) <= max_width:
        return text

    ellipsis = "..."
    for length in range(len(text) - 1, -1, -1):
        candidate = text[:length].rstrip() + ellipsis
        if graphics.measure_text(candidate, scale) <= max_width:
            return candidate
    return ""


def draw():
    graphics.set_pen(WHITE)
    graphics.clear()

    if image_error:
        draw_error(image_error)
    else:
        try:
            jpeg = jpegdec.JPEG(graphics)
            jpeg.open_file(FILENAME)
            jpeg.decode()
        except (OSError, RuntimeError) as error:
            print(error)
            draw_error("Unable to display image")
        else:
            if image_metadata:
                graphics.set_pen(WHITE)
                graphics.rectangle(0, HEIGHT - 25, WIDTH, 25)
                graphics.set_pen(BLACK)
                visible_metadata = _truncate_text(image_metadata, WIDTH - 10, 2)
                graphics.text(visible_metadata, 5, HEIGHT - 20, scale=2)

    gc.collect()
    graphics.update()


def sleep_until_update():
    wlan.disconnect()
    wlan.active(False)
    ih.sleep(UPDATE_INTERVAL)


def main():
    global graphics, WIDTH, HEIGHT

    graphics = PicoGraphics(DISPLAY)
    WIDTH, HEIGHT = graphics.get_bounds()
    graphics.set_font("bitmap8")
    connect_wifi()

    while True:
        update()
        draw()
        sleep_until_update()


if __name__ == "__main__":
    main()
