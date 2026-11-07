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

The service listens on port `8000`; request
`http://your-service-host:8000/image.jpg`. Configure the frame to request the
same URL. The container health check uses a separate `/healthz` endpoint and
does not make extra requests to Immich.

## Image processing

For each request, the service searches Immich for random image assets, selects
one, and downloads its preview thumbnail. It applies EXIF orientation, a
centered aspect-preserving crop, resizes to exactly 800×480, then quantizes
and dithers to `#000000`, `#FFFFFF`, `#A02020`, `#F0E050`, `#608050`, and
`#5080B8`. The result is encoded as high-quality JPEG with chroma subsampling
disabled and returned as `image/jpeg`. JPEG compression may slightly alter
the palette colors; verify color fidelity on the actual frame.

Immich or image-processing failures return a JSON error with HTTP 502.
Missing Immich configuration returns HTTP 503.

## Local tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
```
