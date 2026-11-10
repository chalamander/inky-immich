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
        connection_error = None
        try:
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        except OSError as error:
            connection_error = _format_error("Wi-Fi connection", error)
            print(connection_error)

        for _ in range(WIFI_CONNECT_TIMEOUT_SECONDS):
            if wlan.isconnected():
                print("Wi-Fi connected: {}".format(wlan.ifconfig()))
                return
            time.sleep(1)

        unit = "second" if WIFI_CONNECT_TIMEOUT_SECONDS == 1 else "seconds"
        timeout_message = "Wi-Fi connection timed out after {} {}.".format(
            WIFI_CONNECT_TIMEOUT_SECONDS,
            unit,
        )
        if connection_error:
            timeout_message += "\n" + connection_error
        print(timeout_message)
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


def _format_error(stage, error):
    detail = str(error).strip()
    if not detail:
        detail = repr(error)
    message = "{} failed: {}: {}".format(
        stage,
        error.__class__.__name__,
        detail,
    )

    status = getattr(error, "code", None)
    if status is None:
        status = getattr(error, "status", None)
    if status is None:
        status = getattr(error, "status_code", None)
    if status is not None:
        message += " (HTTP {})".format(status)
        read_error = getattr(error, "read", None)
        if callable(read_error):
            try:
                body = read_error(513)
            except Exception as body_error:
                message += "\nCould not read error response: {}: {}".format(
                    body_error.__class__.__name__,
                    str(body_error) or repr(body_error),
                )
            else:
                if body:
                    truncated = len(body) > 512
                    body = body[:512]
                    if isinstance(body, bytes):
                        body = body.decode("utf-8", "replace")
                    message += "\nResponse: {}".format(body)
                    if truncated:
                        message += "..."
    return message


def update():
    global image_error, image_metadata

    image_error = None
    image_metadata = ""
    for attempt in range(MAX_FETCH_ATTEMPTS):
        stage = "Metadata request to {}".format(_metadata_url().split("?", 1)[0])
        try:
            metadata_response = _urlopen(_metadata_url())
            try:
                stage = "Reading metadata response"
                metadata = load(metadata_response)
            finally:
                metadata_response.close()
            if metadata.get("showMetadata"):
                image_url = _image_url_for_asset(metadata)
            else:
                image_url = IMAGE_URL

            stage = "Image request to {}".format(image_url.split("?", 1)[0])
            response = _urlopen(image_url)
            try:
                stage = "Downloading image"
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
        except Exception as error:
            error_details = _format_error(stage, error)
            print(error_details)
            if attempt == MAX_FETCH_ATTEMPTS - 1:
                image_error = "Fetch failed after {} attempts.\n{}".format(
                    MAX_FETCH_ATTEMPTS,
                    error_details,
                )
            else:
                time.sleep(RETRY_DELAY_SECONDS)
        else:
            break
        gc.collect()

    gc.collect()


def draw_error(message):
    graphics.set_pen(RED)
    graphics.rectangle(0, 0, WIDTH, HEIGHT)
    graphics.set_pen(BLACK)
    scale = 2
    max_width = WIDTH - 10
    lines = []
    for paragraph in message.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = word if not line else line + " " + word
            if graphics.measure_text(candidate, scale) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for character in word:
                candidate = line + character
                if graphics.measure_text(candidate, scale) > max_width and line:
                    lines.append(line)
                    line = character
                else:
                    line = candidate
        lines.append(line)

    line_height = 18
    max_lines = HEIGHT // line_height
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        final_line = lines[-1]
        while (
            final_line
            and graphics.measure_text(final_line + "...", scale) > max_width
        ):
            final_line = final_line[:-1]
        lines[-1] = final_line + "..."

    for index, line in enumerate(lines):
        graphics.text(line, 5, 5 + index * line_height, scale=scale)


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
        except Exception as error:
            error_details = _format_error("Displaying JPEG {}".format(FILENAME), error)
            print(error_details)
            draw_error(error_details)
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
