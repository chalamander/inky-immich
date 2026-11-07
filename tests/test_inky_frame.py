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
    inky_frame.BLACK = 0
    inky_frame.RED = 1
    inky_frame.WHITE = 2
    modules = {
        "urllib.urequest": urequest,
        "jpegdec": jpegdec,
        "inky_frame": inky_frame,
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
            side_effect=[OSError("network unreachable"), response]
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
                call(frame.IMAGE_URL, timeout=frame.REQUEST_TIMEOUT_SECONDS),
                call(frame.IMAGE_URL, timeout=frame.REQUEST_TIMEOUT_SECONDS),
            ],
        )
        sleep.assert_called_once_with(frame.RETRY_DELAY_SECONDS)
        response.close.assert_called_once()

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
