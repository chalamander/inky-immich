# inky-immich

A small web service that fetches a random Immich photo and prepares it for an
800×480 Inky Frame. The frame can request a ready-to-display JPEG from
`GET /image.jpg`.

## Create an Immich API key

In order to get things started, Create an API key in your Immich installation
and set the following permissions:

| Permission | Description |
|---|---|
| `AssetRead` | Required to search for images and read their thumbnails. |
| `AlbumRead` | Optional; required to look up album names for the metadata overlay. |

Keep the key out of source control. `IMMICH_URL` is the Immich server origin,
with no `/api` suffix; the service also accepts a URL that already ends in
`/api`.

## Configure and run

Create a `.env` file in this directory (it is ignored by Git):

```dotenv
IMMICH_URL=http://your-immich-server:2283
IMMICH_API_KEY=your-immich-api-key
```

Start the service with Docker Compose:

```sh
docker compose up --build -d
```

## Configure image selection

The service listens on host port `18765` (container port `8000`). The frame
requests images from `http://your-service-host:18765/image.jpg`. This endpoint
accepts the following optional query parameters:

| Parameter | Description |
|---|---|
| `orientation` | Select `landscape`, `portrait`, or `both` images. Defaults to `both`. Portrait images are rotated for a screen mounted at -90°. |
| `people` | Filter to images containing any of the specified Immich person IDs. Supply multiple IDs as a comma-separated list or repeat the parameter. |
| `includeTags` | Include images matching any of the specified Immich tag IDs. Accepts comma-separated IDs or repeated parameters. |
| `excludeTags` | Exclude images matching any of the specified Immich tag IDs. Accepts comma-separated IDs or repeated parameters. |
| `excludeDarkerImages` | Set to `true` to skip images whose average grayscale brightness is below 80/255. Defaults to `false`. |
| `showMetadata` | Set to `true` to enable the Inky Frame metadata overlay. Defaults to `false`; metadata includes the capture date, location, and first album when available. |

For example, `http://your-service-host:18765/image.jpg?orientation=portrait`
returns a portrait source image rotated 90° clockwise for a screen mounted at
-90°. Multiple people or tags can be supplied as comma-separated values or by
repeating the parameter. The `people` filter matches any listed person;
`includeTags` and `excludeTags` can be combined. If there are no images in the
requested orientation, the endpoint returns an error. Images are returned at
800×480.
When `excludeDarkerImages=true`, the service checks candidate preview
thumbnails and skips any with average grayscale brightness below 80/255.

The container health check uses a separate `/healthz` endpoint and does not
make extra requests to Immich.

## Get a person’s ID from Immich

1. Open Immich’s web interface and click on “Explore” in the left-hand navigation.
2. Click on the person you want the ID of (you may have to click “view all” if you don’t see them).
3. The URL will look something like this:
   `http://192.168.86.123:2283/people/a04175f4-97bb-4d97-8d49-3700263043e5`.
4. The person’s ID is everything after `people/`; in this example:
   `a04175f4-97bb-4d97-8d49-3700263043e5`.

## Image metadata overlay

Metadata display is off by default. To enable it, add
`showMetadata=true` in the image URL. When enabled, the frame requests the
selected image's metadata and then fetches that same image, so the displayed
details match the photo. The bottom of the screen shows the capture date,
location, and first album name (when available), separated by commas. Missing
details are omitted. Dates use an ordinal day and written month, e.g.
`6th May 2024`. Album names require the API key's `AlbumRead` permission.
The metadata endpoint uses the same orientation, people, and tag filters as
`/image.jpg`.

## Set up the Inky Frame

First, follow the [Pimoroni Inky Frame setup guide](https://learn.pimoroni.com/article/getting-started-with-inky-frame)
to prepare the device and set up Thonny. Open `immich_inky_frame.py` in Thonny
and copy/save it to the Inky Frame.

Before copying the script, change `IMAGE_URL` in the Python file to the
`/image.jpg` URL for the server running inky-immich, for example
`http://your-service-host:18765/image.jpg`. Add any query parameters from
[Configure image selection](#configure-image-selection) that you want to use.

Once the script is on the device and the web server is running, the Inky Frame
should retrieve and display images from the `/image.jpg` endpoint. If a
download fails, the frame retries after a 10-second interval and shows an error
after 20 failed attempts. The Inky Frame's `urequest.urlopen` implementation
does not support a request timeout argument.

## Image processing

For each request, the service searches Immich for random image assets and
downloads a preview thumbnail. When a specific orientation is requested, it
selects a thumbnail with that orientation after applying EXIF rotation. The
service applies EXIF orientation and, for portrait requests, rotates the image
90° clockwise for a screen mounted at -90°. It then performs a centered
aspect-preserving crop/resize to 800×480, lifts midtones with gamma correction,
increases contrast and saturation, then applies Floyd–Steinberg dithering to
the Inky Frame's seven display colors. The frame disables the JPEG decoder's
default ordered dithering to avoid dithering the image twice. The result is
encoded as JPEG at quality 100 with chroma subsampling disabled and returned
as `image/jpeg`.

Immich or image-processing failures return a JSON error with HTTP 502.
Missing Immich configuration returns HTTP 503.

## Local tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```
