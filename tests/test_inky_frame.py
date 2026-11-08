import importlib.util
import sys
import types
import unittest
import urllib
from pathlib import Path
from unittest.mock import Mock, call, mock_open, patch


FRAME_PATH = Path(__file__).resolve().parents[1] / "immich_inky_frame.py"


def load_frame_module():
    urequest = types.ModuleType("urllib.urequest")
    urequest.urlopen = None
    jpegdec = types.ModuleType("jpegdec")
    inky_frame = types.ModuleType("inky_frame")
    ujson = types.ModuleType("ujson")
    inky_frame.BLACK = 0
    inky_frame.RED = 1
    inky_frame.WHITE = 2
    ujson.load = lambda response: response.metadata
    modules = {
        "urllib.urequest": urequest,
        "jpegdec": jpegdec,
        "inky_frame": inky_frame,
        "ujson": ujson,
    }

    spec = importlib.util.spec_from_file_location("inky_frame_under_test", FRAME_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.object(
        urllib,
        "urequest",
        urequest,
        create=True,
    ):
        spec.loader.exec_module(module)
    return module, urequest


class FrameRetryTests(unittest.TestCase):
    def test_retries_after_failure_and_clears_error_after_success(self):
        frame, urequest = load_frame_module()
        frame.IMAGE_URL = (
            "http://your-service-host:18765/image.jpg"
            "?orientation=portrait&people=person-id&includeTags=tag-id"
        )
        metadata_response = types.SimpleNamespace(
            metadata={
                "showMetadata": True,
                "assetId": "asset-id",
                "orientation": "portrait",
                "excludeDarkerImages": False,
                "date": "2024-05-06",
                "location": "London",
                "album": "Holiday",
            }
        )
        metadata_response.close = Mock()
        response = types.SimpleNamespace()
        payload = bytearray(b"\xff\xd8")

        def readinto(buffer):
            if not payload:
                return 0
            buffer[:len(payload)] = payload
            count = len(payload)
            payload.clear()
            return count

        response.readinto = readinto
        response.close = Mock()
        urequest.urlopen = Mock(
            side_effect=[OSError("network unreachable"), metadata_response, response]
        )

        with (
            patch("builtins.open", mock_open()),
            patch.object(frame.time, "sleep") as sleep,
            patch("builtins.print"),
        ):
            frame.update()

        self.assertIsNone(frame.image_error)
        self.assertEqual(
            urequest.urlopen.call_args_list,
            [
                call(
                    "http://your-service-host:18765/image-metadata.json"
                    "?orientation=portrait&people=person-id&includeTags=tag-id"
                ),
                call(
                    "http://your-service-host:18765/image-metadata.json"
                    "?orientation=portrait&people=person-id&includeTags=tag-id"
                ),
                call(
                    "http://your-service-host:18765/image.jpg"
                    "?assetId=asset-id&orientation=portrait&excludeDarkerImages=false"
                ),
            ],
        )
        self.assertEqual(frame.image_metadata, "2024-05-06, London, Holiday")
        sleep.assert_called_once_with(frame.RETRY_DELAY_SECONDS)
        metadata_response.close.assert_called_once()
        response.close.assert_called_once()

    def test_metadata_is_off_by_default(self):
        frame, urequest = load_frame_module()
        settings_response = types.SimpleNamespace(metadata={"showMetadata": False})
        settings_response.close = Mock()
        response = types.SimpleNamespace()
        payload = bytearray(b"\xff\xd8")

        def readinto(buffer):
            if not payload:
                return 0
            buffer[:len(payload)] = payload
            count = len(payload)
            payload.clear()
            return count

        response.readinto = readinto
        response.close = Mock()
        urequest.urlopen = Mock(side_effect=[settings_response, response])

        with patch("builtins.open", mock_open()):
            frame.update()

        self.assertEqual(
            urequest.urlopen.call_args_list,
            [
                call("http://your-service-host:18765/image-metadata.json"),
                call(frame.IMAGE_URL),
            ],
        )
        settings_response.close.assert_called_once()
        self.assertEqual(frame.image_metadata, "")

    def test_draw_overlays_metadata_in_bottom_banner(self):
        frame, _ = load_frame_module()
        frame.graphics = Mock()
        jpeg = Mock()
        frame.jpegdec.JPEG = Mock(return_value=jpeg)
        frame.image_metadata = "2024-05-06, London, Holiday"

        frame.draw()

        jpeg.decode.assert_called_once_with()
        frame.graphics.rectangle.assert_called_once_with(
            0,
            frame.HEIGHT - 25,
            frame.WIDTH,
            25,
        )
        frame.graphics.text.assert_called_once_with(
            frame.image_metadata,
            5,
            frame.HEIGHT - 20,
            frame.WIDTH - 10,
            2,
        )

    def test_shows_error_only_after_all_attempts_fail(self):
        frame, urequest = load_frame_module()
        urequest.urlopen = Mock(side_effect=OSError("network unreachable"))

        with patch.object(frame.time, "sleep") as sleep, patch("builtins.print"):
            frame.update()

        self.assertEqual(urequest.urlopen.call_count, frame.MAX_FETCH_ATTEMPTS)
        self.assertEqual(sleep.call_count, frame.MAX_FETCH_ATTEMPTS - 1)
        self.assertEqual(frame.image_error, "Unable to fetch image")


if __name__ == "__main__":
    unittest.main()
