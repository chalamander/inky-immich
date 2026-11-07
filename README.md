# inky-immich

A small web service that fetches a random Immich photo and prepares it for an
800×480 Inky Frame. The frame can request a ready-to-display JPEG from
`GET /image.jpg`.

## Configure and run

Create a `.env` file in this directory (it is ignored by Git):

```dotenv
IMMICH_URL=http://your-immich-server:2283
IMMICH_API_KEY=your-immich-api-key
```

`IMMICH_URL` is the Immich server origin, with no `/api` suffix. The service
also accepts a URL that already ends in `/api`. Create an API key in Immich
with permission to search and read assets. Keep the key out of source control.

Start the service with Docker Compose:

```sh
docker compose up --build -d
```

The service listens on host port `18765` (container port `8000`); request
`http://your-service-host:18765/image.jpg`. Set `IMAGE_URL` in
[`immich_inky_frame.py`](./immich_inky_frame.py) to this address, replacing the
default host if needed. The container health check uses a separate `/healthz`
endpoint and does not make extra requests to Immich.

The `/image.jpg` endpoint accepts an optional `orientation` query parameter:
`landscape`, `portrait`, or `both`. It defaults to `both`; for example,
`http://your-service-host:18765/image.jpg?orientation=portrait` returns a
portrait source image processed for the frame. If Immich's random results have
no image in the requested orientation, the endpoint returns an error.

## Set up the Inky Frame

First, follow the [Pimoroni Inky Frame setup guide](https://learn.pimoroni.com/article/getting-started-with-inky-frame)
to prepare the device and set up Thonny. Open `immich_inky_frame.py` in Thonny
and copy/save it to the Inky Frame.

Before copying the script, change `IMAGE_URL` in the Python file to the
`/image.jpg` URL for the server running inky-immich, for example
`http://your-service-host:18765/image.jpg`.

Once the script is on the device and the web server is running, the Inky Frame
should retrieve and display images from the `/image.jpg` endpoint.

## Image processing

For each request, the service searches Immich for random image assets and
downloads a preview thumbnail. When a specific orientation is requested, it
selects a thumbnail with that orientation after applying EXIF rotation. The
service applies EXIF orientation and a centered aspect-preserving crop/resize
to exactly 800×480. It does not
apply color enhancement, sharpening, palette quantization, or dithering. The
image is encoded as JPEG at quality 100 with chroma subsampling disabled to
preserve source detail as much as JPEG allows, then returned as `image/jpeg`.

Immich or image-processing failures return a JSON error with HTTP 502.
Missing Immich configuration returns HTTP 503.

## Local tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```
