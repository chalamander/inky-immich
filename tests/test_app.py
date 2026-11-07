import io
import os
import unittest
from unittest.mock import patch

from PIL import Image

import app


def make_image(size, color, image_format="JPEG", exif=None):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    options = {"format": image_format}
    if exif is not None:
        options["exif"] = exif
    image.save(output, **options)
    return output.getvalue()


class ProcessImageTests(unittest.TestCase):
    def test_display_preparation_boosts_contrast_color_and_edges(self):
        grayscale = Image.new("RGB", (100, 10))
        muted_color = Image.new("RGB", (100, 10))
        for x in range(100):
            level = 105 + x // 2
            grayscale.paste((level, level, level), (x, 0, x + 1, 10))
            muted_color.paste((level, level - 10, level - 10), (x, 0, x + 1, 10))

        prepared_grayscale = app._prepare_for_display(grayscale)
        prepared_color = app._prepare_for_display(muted_color)
        input_contrast = grayscale.getextrema()[0][1] - grayscale.getextrema()[0][0]
        output_contrast = (
            prepared_grayscale.getextrema()[0][1] - prepared_grayscale.getextrema()[0][0]
        )
        input_saturation = muted_color.getpixel((75, 5))[0] - muted_color.getpixel((75, 5))[1]
        output_saturation = (
            prepared_color.getpixel((75, 5))[0] - prepared_color.getpixel((75, 5))[1]
        )
        self.assertGreater(output_contrast, input_contrast)
        self.assertGreater(output_saturation, input_saturation)

        edge = Image.new("RGB", (20, 10), (100, 100, 100))
        for x in range(10, 20):
            edge.paste((150, 150, 150), (x, 0, x + 1, 10))
        prepared_edge = app._prepare_for_display(edge)
        input_edge = edge.getpixel((10, 5))[0] - edge.getpixel((9, 5))[0]
        output_edge = (
            prepared_edge.getpixel((10, 5))[0] - prepared_edge.getpixel((9, 5))[0]
        )
        self.assertGreater(output_edge, input_edge)

    def test_outputs_jpeg_at_target_size_for_varied_inputs(self):
        cases = [
            ((240, 480), "portrait"),
            ((1200, 675), "landscape"),
            ((13, 1700), "unusually sized"),
        ]
        for size, name in cases:
            with self.subTest(name=name):
                result = app.process_image(make_image(size, (160, 90, 40)))
                with Image.open(io.BytesIO(result)) as output:
                    self.assertEqual(output.format, "JPEG")
                    self.assertEqual(output.size, (800, 480))
                    self.assertEqual(output.mode, "RGB")

    def test_applies_exif_orientation_before_fit(self):
        pixels = Image.new("RGB", (120, 60), "black")
        for x in range(60, 120):
            for y in range(60):
                pixels.putpixel((x, y), (255, 255, 255))
        exif = Image.Exif()
        exif[274] = 6
        source = io.BytesIO()
        pixels.save(source, format="JPEG", quality=100, exif=exif)

        result = app.process_image(source.getvalue())

        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.size, (800, 480))
            self.assertEqual(output.getpixel((400, 30))[0], 0)
            self.assertEqual(output.getpixel((400, 450))[0], 255)

    def test_colors_are_quantized_to_target_palette(self):
        palette = [
            (0, 0, 0),
            (255, 255, 255),
            (160, 32, 32),
            (240, 224, 80),
            (96, 128, 80),
            (80, 128, 184),
        ]
        result = app.process_image(make_image((800, 480), (160, 32, 32)))

        with Image.open(io.BytesIO(result)) as output:
            pixel = output.getpixel((400, 240))
        distance = min(
            sum(abs(channel - target) for channel, target in zip(pixel, color))
            for color in palette
        )
        self.assertLessEqual(distance, 8)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        app.app.config.update(TESTING=True)
        self.client = app.app.test_client()

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_returns_processed_thumbnail(self, mock_post, mock_get):
        mock_post.return_value.json.return_value = [{"id": "asset-id"}]
        mock_get.return_value.content = make_image((400, 300), (120, 130, 140))

        response = self.client.get("/image.jpg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        with Image.open(io.BytesIO(response.data)) as output:
            self.assertEqual(output.size, (800, 480))
        self.assertEqual(mock_post.call_args.args[0], "http://immich:2283/api/search/random")
        self.assertEqual(
            mock_get.call_args.args[0],
            "http://immich:2283/api/assets/asset-id/thumbnail",
        )
        self.assertEqual(mock_get.call_args.kwargs["params"], {"size": "preview"})

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration_returns_clear_error(self):
        response = self.client.get("/image.jpg")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"],
            "IMMICH_URL and IMMICH_API_KEY must both be configured.",
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.post")
    def test_immich_failure_returns_bad_gateway(self, mock_post):
        mock_post.return_value.status_code = 500
        mock_post.return_value.raise_for_status.side_effect = app.requests.HTTPError()

        response = self.client.get("/image.jpg")

        self.assertEqual(response.status_code, 502)
        self.assertIn("Immich random image search failed", response.get_json()["error"])

    def test_health_check_does_not_depend_on_immich(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_invalid_thumbnail_returns_clear_processing_error(self, mock_post, mock_get):
        mock_post.return_value.json.return_value = [{"id": "asset-id"}]
        mock_get.return_value.content = b"not an image"

        response = self.client.get("/image.jpg")

        self.assertEqual(response.status_code, 502)
        self.assertIn("could not be processed", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
