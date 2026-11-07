import gc
from urllib import urequest

import jpegdec
from inky_frame import BLACK, RED, WHITE

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

image_error = None


def update():
    global image_error

    try:
        response = urequest.urlopen(IMAGE_URL)
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
            image_error = None
        finally:
            response.close()
        gc.collect()
    except (OSError, ValueError) as error:
        print(error)
        image_error = "Unable to fetch image"


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
            jpeg.decode()
        except (OSError, RuntimeError) as error:
            print(error)
            draw_error("Unable to display image")

    gc.collect()
    graphics.update()
