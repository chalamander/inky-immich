import io
import os
import unittest
from unittest.mock import Mock, patch

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

    def test_increases_saturation(self):
        source_color = (123, 77, 189)
        result = app.process_image(make_image((800, 480), source_color))

        with Image.open(io.BytesIO(result)) as output:
            pixel = output.getpixel((400, 240))

        self.assertGreater(pixel[2] - pixel[0], source_color[2] - source_color[0])

    def test_shadow_lift_brightens_shadows_and_preserves_highlights(self):
        shadow = make_image((800, 480), (40, 40, 40))
        highlight = make_image((800, 480), (200, 200, 200))

        with Image.open(io.BytesIO(app.process_image(shadow))) as output:
            shadow_pixel = output.getpixel((400, 240))
        with Image.open(io.BytesIO(app.process_image(highlight))) as output:
            highlight_pixel = output.getpixel((400, 240))

        self.assertGreater(shadow_pixel[0], 40)
        self.assertLessEqual(max(shadow_pixel) - min(shadow_pixel), 2)
        self.assertAlmostEqual(highlight_pixel[0], 200, delta=2)
        self.assertLessEqual(max(highlight_pixel) - min(highlight_pixel), 2)

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
            self.assertAlmostEqual(
                output.getpixel((400, 30))[0],
                app.SHADOW_LUT[0],
                delta=2,
            )
            self.assertEqual(output.getpixel((400, 450))[0], 255)

    def test_orientation_matching_applies_exif_rotation(self):
        exif = Image.Exif()
        exif[274] = 6
        image_bytes = make_image((120, 60), (30, 40, 50), exif=exif)

        self.assertTrue(app._matches_orientation(image_bytes, "portrait"))
        self.assertFalse(app._matches_orientation(image_bytes, "landscape"))

    def test_portrait_images_are_rotated_clockwise_and_fit_landscape_output(self):
        source = Image.new("RGB", (300, 500), "red")
        for y in range(250, 500):
            for x in range(300):
                source.putpixel((x, y), (0, 0, 255))
        image_bytes = io.BytesIO()
        source.save(image_bytes, format="JPEG", quality=100)

        result = app.process_image(image_bytes.getvalue(), "portrait")

        with Image.open(io.BytesIO(result)) as output:
            self.assertEqual(output.size, (800, 480))
            left_pixel = output.getpixel((100, 240))
            right_pixel = output.getpixel((700, 240))
        self.assertGreater(left_pixel[2], left_pixel[0])
        self.assertGreater(right_pixel[0], right_pixel[2])


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
            mock_post.call_args.kwargs["json"],
            {"size": 20, "filter": {"type": {"eq": "IMAGE"}}},
        )
        self.assertEqual(
            mock_get.call_args.args[0],
            "http://immich:2283/api/assets/asset-id/thumbnail",
        )
        self.assertEqual(mock_get.call_args.kwargs["params"], {"size": "preview"})

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_people_parameter_filters_for_any_person_id(self, mock_post, mock_get):
        mock_post.return_value.json.return_value = [{"id": "asset-id"}]
        mock_get.return_value.content = make_image((400, 300), (120, 130, 140))

        people_ids = [
            "a04175f4-97bb-4d97-8d49-3700263043e5",
            "b04175f4-97bb-4d97-8d49-3700263043e5",
            "c04175f4-97bb-4d97-8d49-3700263043e5",
        ]
        response = self.client.get(
            f"/image.jpg?people={people_ids[0]},{people_ids[1]}&people={people_ids[2]}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "size": 20,
                "filter": {
                    "type": {"eq": "IMAGE"},
                    "personIds": {"any": people_ids},
                },
            },
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_tag_parameters_include_any_and_exclude_any_matching_tag(
        self,
        mock_post,
        mock_get,
    ):
        mock_post.return_value.json.return_value = [{"id": "asset-id"}]
        mock_get.return_value.content = make_image((400, 300), (120, 130, 140))
        include_tags = [
            "a04175f4-97bb-4d97-8d49-3700263043e5",
            "b04175f4-97bb-4d97-8d49-3700263043e5",
            "c04175f4-97bb-4d97-8d49-3700263043e5",
        ]
        exclude_tags = [
            "d04175f4-97bb-4d97-8d49-3700263043e5",
            "e04175f4-97bb-4d97-8d49-3700263043e5",
        ]

        response = self.client.get(
            "/image.jpg"
            f"?includeTags={include_tags[0]},{include_tags[1]}"
            f"&includeTags={include_tags[2]}"
            f"&excludeTags={exclude_tags[0]},{exclude_tags[1]}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "size": 20,
                "filter": {
                    "type": {"eq": "IMAGE"},
                    "tagIds": {"any": include_tags, "none": exclude_tags},
                },
            },
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.random.shuffle")
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_orientation_parameter_skips_thumbnails_with_the_wrong_orientation(
        self,
        mock_post,
        mock_get,
        mock_shuffle,
    ):
        mock_post.return_value.json.return_value = [
            {"id": "portrait"},
            {"id": "landscape"},
        ]
        mock_shuffle.side_effect = lambda candidates: None
        mock_get.side_effect = [
            Mock(content=make_image((300, 500), (10, 20, 30))),
            Mock(content=make_image((500, 300), (40, 50, 60))),
        ]

        response = self.client.get("/image.jpg?orientation=landscape")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("/assets/landscape/thumbnail", mock_get.call_args.args[0])

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_portrait_orientation_returns_portrait_thumbnail(self, mock_post, mock_get):
        mock_post.return_value.json.return_value = [{"id": "portrait"}]
        mock_get.return_value.content = make_image((300, 500), (10, 20, 30))

        response = self.client.get("/image.jpg?orientation=portrait")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/assets/portrait/thumbnail", mock_get.call_args.args[0])
        with Image.open(io.BytesIO(response.data)) as output:
            self.assertEqual(output.size, (800, 480))

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.random.shuffle", side_effect=lambda candidates: None)
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_orientation_parameter_returns_error_when_no_candidate_matches(
        self,
        mock_post,
        mock_get,
        _mock_shuffle,
    ):
        mock_post.return_value.json.return_value = [{"id": "landscape"}]
        mock_get.return_value.content = make_image((500, 300), (40, 50, 60))

        response = self.client.get("/image.jpg?orientation=portrait")

        self.assertEqual(response.status_code, 502)
        self.assertIn("no portrait images", response.get_json()["error"])

    def test_invalid_orientation_returns_bad_request(self):
        response = self.client.get("/image.jpg?orientation=square")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "orientation must be landscape, portrait, or both.",
        )

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
