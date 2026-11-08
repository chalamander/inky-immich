import io
import os
import random
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageStat

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

    def test_gamma_and_contrast_lift_dark_images(self):
        shadow = make_image((800, 480), (40, 40, 40))
        midtone = make_image((800, 480), (120, 120, 120))
        highlight = make_image((800, 480), (200, 200, 200))

        with Image.open(io.BytesIO(app.process_image(shadow))) as output:
            shadow_mean = ImageStat.Stat(output.convert("L")).mean[0]
        with Image.open(io.BytesIO(app.process_image(midtone))) as output:
            midtone_mean = ImageStat.Stat(output.convert("L")).mean[0]
        with Image.open(io.BytesIO(app.process_image(highlight))) as output:
            highlight_mean = ImageStat.Stat(output.convert("L")).mean[0]

        self.assertGreater(shadow_mean, 40)
        self.assertGreater(midtone_mean, 120)
        self.assertGreater(highlight_mean, 200)

    def test_processed_jpeg_is_small_enough_for_frame_storage(self):
        source = Image.new("RGB", (800, 480))
        rng = random.Random(41)
        source.putdata([
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(800 * 480)
        ])
        image_bytes = io.BytesIO()
        source.save(image_bytes, format="JPEG", quality=90)

        result = app.process_image(image_bytes.getvalue())

        self.assertLess(len(result), 400 * 1024)

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
            self.assertLessEqual(output.getpixel((400, 30))[0], 3)
            self.assertEqual(output.getpixel((400, 450))[0], 255)

    def test_orientation_matching_applies_exif_rotation(self):
        exif = Image.Exif()
        exif[274] = 6
        image_bytes = make_image((120, 60), (30, 40, 50), exif=exif)

        self.assertTrue(app._matches_orientation(image_bytes, "portrait"))
        self.assertFalse(app._matches_orientation(image_bytes, "landscape"))

    def test_minimum_brightness_excludes_dark_images(self):
        dark = make_image((80, 60), (45, 45, 45))
        bright = make_image((80, 60), (120, 120, 120))

        self.assertFalse(app._matches_minimum_brightness(dark))
        self.assertTrue(app._matches_minimum_brightness(bright))

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


class MetadataFormattingTests(unittest.TestCase):
    def test_formats_dates_with_ordinal_day_and_written_month(self):
        cases = {
            "2024-05-01T12:30:00.000Z": "1st May 2024",
            "2024-08-02T12:30:00.000Z": "2nd August 2024",
            "2024-03-03T12:30:00.000Z": "3rd March 2024",
            "2024-05-06T12:30:00.000Z": "6th May 2024",
            "2024-08-11T12:30:00.000Z": "11th August 2024",
            "2024-08-12T12:30:00.000Z": "12th August 2024",
            "2024-08-13T12:30:00.000Z": "13th August 2024",
            "2024-08-21T12:30:00.000Z": "21st August 2024",
        }

        for created_at, expected in cases.items():
            with self.subTest(created_at=created_at):
                self.assertEqual(app._format_image_date(created_at), expected)

    def test_missing_or_invalid_date_is_omitted(self):
        self.assertEqual(app._format_image_date(None), "")
        self.assertEqual(app._format_image_date("not-a-date"), "")

    def test_removes_emoji_sequences_from_album_titles(self):
        cases = {
            "Summer 🌴 Trip": "Summer Trip",
            "Family 👨‍👩‍👧‍👦": "Family",
            "Travel 🇬🇧": "Travel",
            "Keycap 1️⃣": "Keycap",
            "Copyright ©️ 2026": "Copyright 2026",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(app._remove_emojis(title), expected)

    def test_preserves_non_emoji_title_characters(self):
        self.assertEqual(app._remove_emojis("Summer © 2026"), "Summer © 2026")


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
    @patch("app.random.shuffle", side_effect=lambda candidates: None)
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_exclude_darker_images_skips_dark_candidates(
        self,
        mock_post,
        mock_get,
        _mock_shuffle,
    ):
        mock_post.return_value.json.return_value = [
            {"id": "dark"},
            {"id": "bright"},
        ]
        mock_get.side_effect = [
            Mock(content=make_image((80, 60), (45, 45, 45))),
            Mock(content=make_image((80, 60), (120, 120, 120))),
        ]

        response = self.client.get("/image.jpg?excludeDarkerImages=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("/assets/bright/thumbnail", mock_get.call_args.args[0])

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_exclude_darker_images_returns_error_when_all_candidates_are_dark(
        self,
        mock_post,
        mock_get,
    ):
        mock_post.return_value.json.return_value = [{"id": "dark"}]
        mock_get.return_value.content = make_image((80, 60), (45, 45, 45))

        response = self.client.get("/image.jpg?excludeDarkerImages=true")

        self.assertEqual(response.status_code, 502)
        self.assertIn("minimum average brightness", response.get_json()["error"])

    def test_exclude_darker_images_rejects_invalid_boolean(self):
        response = self.client.get("/image.jpg?excludeDarkerImages=perhaps")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            "excludeDarkerImages must be a boolean.",
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_metadata_endpoint_returns_date_location_and_first_album(
        self,
        mock_post,
        mock_get,
    ):
        asset_id = "a04175f4-97bb-4d97-8d49-3700263043e5"
        mock_post.return_value.json.return_value = [{
            "id": asset_id,
            "fileCreatedAt": "2024-05-06T12:30:00.000Z",
            "exifInfo": {
                "city": "London",
                "state": "Greater London",
                "country": "United Kingdom",
            },
        }]
        mock_get.side_effect = [
            Mock(content=make_image((300, 500), (120, 130, 140))),
            Mock(json=Mock(return_value=[
                {"albumName": "First 🌄 album"},
                {"albumName": "Second album"},
            ])),
        ]

        response = self.client.get("/image-metadata.json?orientation=portrait")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "assetId": asset_id,
                "orientation": "portrait",
                "date": "6th May 2024",
                "location": "London / Greater London / United Kingdom",
                "album": "First album",
            },
        )
        self.assertEqual(mock_get.call_args_list[1].args[0], "http://immich:2283/api/albums")
        self.assertEqual(
            mock_get.call_args_list[1].kwargs["params"],
            {"assetId": asset_id},
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_image_endpoint_can_fetch_metadata_selected_asset(self, mock_post, mock_get):
        asset_id = "a04175f4-97bb-4d97-8d49-3700263043e5"
        mock_get.return_value.content = make_image((400, 300), (120, 130, 140))

        response = self.client.get(f"/image.jpg?assetId={asset_id}")

        self.assertEqual(response.status_code, 200)
        mock_post.assert_not_called()
        self.assertEqual(
            mock_get.call_args.args[0],
            f"http://immich:2283/api/assets/{asset_id}/thumbnail",
        )

    @patch.dict(os.environ, {"IMMICH_URL": "http://immich:2283", "IMMICH_API_KEY": "test-key"})
    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_selected_asset_respects_orientation_filter(self, mock_post, mock_get):
        asset_id = "a04175f4-97bb-4d97-8d49-3700263043e5"
        mock_get.return_value.content = make_image((400, 300), (120, 130, 140))

        response = self.client.get(
            f"/image.jpg?assetId={asset_id}&orientation=portrait"
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("no portrait images", response.get_json()["error"])
        mock_post.assert_not_called()

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
