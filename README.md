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
# Optional image-selection defaults:
ORIENTATION=both
EXCLUDE_DARKER_IMAGES=false
# Optional filters; replace with IDs from your Immich installation:
# PEOPLE=person-id-1,person-id-2
# INCLUDE_TAGS=tag-id-1,tag-id-2
# EXCLUDE_TAGS=tag-id-3
```

The optional variables set server-wide defaults for the corresponding image
URL parameters. A URL parameter takes precedence over its environment variable;
for list parameters, specifying the URL parameter replaces the full list from
the environment. Lists in `.env` use comma-separated values. `PEOPLE`,
`INCLUDE_TAGS`, and `EXCLUDE_TAGS` are intentionally omitted and unset by
default; set them only when you want to filter using IDs from your Immich
installation. `showMetadata` is the exception: it is controlled only by the
URL query parameter and has no environment-variable setting.

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
| `orientation` | Select `landscape`, `portrait`, or `both` images. Defaults to `ORIENTATION`, then `both`. Portrait images are rotated for a screen mounted at -90°. |
| `people` | Filter to images containing any of the specified Immich person IDs. Supply multiple IDs as a comma-separated list or repeat the parameter. Overrides `PEOPLE`. |
| `includeTags` | Include images matching any of the specified Immich tag IDs. Accepts comma-separated IDs or repeated parameters. Overrides `INCLUDE_TAGS`. |
| `excludeTags` | Exclude images matching any of the specified Immich tag IDs. Accepts comma-separated IDs or repeated parameters. Overrides `EXCLUDE_TAGS`. |
| `excludeDarkerImages` | Set to `true` to skip images whose average grayscale brightness is below 80/255. Defaults to `EXCLUDE_DARKER_IMAGES`, then `false`. |
| `showMetadata` | Set to `true` to enable the Inky Frame metadata overlay. This URL-only parameter defaults to `false`; metadata includes the capture date, location, and first album when available. |

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

Metadata display is off by default and can only be enabled by adding
`showMetadata=true` to the image URL. It cannot be set with a Docker Compose
environment variable. When enabled, the frame requests the
selected image's metadata and then fetches that same image, so the displayed
details match the photo. The bottom of the screen shows the capture date,
location, and first album name (when available). The album name is
right-aligned; the date and location remain left-aligned. Missing details are
omitted. Dates use an ordinal day and written month, e.g.
`6th May 2024`. Emoji characters are removed from album titles before display.
Album names require the API key's `AlbumRead` permission.
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
increases contrast and saturation, and returns a full-color JPEG for the
Inky Frame's JPEG decoder to dither to the display's seven colors. The JPEG
uses quality 90 and 4:2:0 chroma subsampling to keep file sizes suitable for
the frame's limited storage.

Immich or image-processing failures return a JSON error with HTTP 502.
Missing Immich configuration returns HTTP 503.

## Local tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```
