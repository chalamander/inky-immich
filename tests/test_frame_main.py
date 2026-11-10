import importlib.util
import sys
import types
import unittest
import urllib
from pathlib import Path
from unittest.mock import Mock, call, mock_open, patch


MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


def load_main_module():
    urequest = types.ModuleType("urllib.urequest")
    urequest.urlopen = None
    inky_helper = types.ModuleType("inky_helper")
    jpegdec = types.ModuleType("jpegdec")
    inky_frame = types.ModuleType("inky_frame")
    network = types.ModuleType("network")
    picographics = types.ModuleType("picographics")
    secrets = types.ModuleType("secrets")
    ujson = types.ModuleType("ujson")
    wlan = Mock()
    inky_helper.sleep = Mock()

    inky_frame.BLACK = 0
    inky_frame.RED = 1
    inky_frame.WHITE = 2
    network.STA_IF = 0
    network.WLAN = Mock(return_value=wlan)
    picographics.DISPLAY_INKY_FRAME_SPECTRA_7 = object()
    picographics.PicoGraphics = Mock()
    secrets.HOST_IP = "192.168.1.50"
    secrets.HOST_PORT = 19000
    secrets.WIFI_SSID = "test-ssid"
    secrets.WIFI_PASSWORD = "test-password"
    ujson.load = lambda response: response.metadata

    modules = {
        "urllib.urequest": urequest,
        "inky_helper": inky_helper,
        "jpegdec": jpegdec,
        "inky_frame": inky_frame,
        "network": network,
        "picographics": picographics,
        "secrets": secrets,
        "ujson": ujson,
    }
    spec = importlib.util.spec_from_file_location("inky_frame_main_under_test", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.object(
        urllib,
        "urequest",
        urequest,
        create=True,
    ):
        spec.loader.exec_module(module)
    return module, urequest, wlan, inky_helper


class FrameMainTests(unittest.TestCase):
    def test_request_failure_forces_wifi_reconnection(self):
        frame, urequest, wlan, _ = load_main_module()
        urequest.urlopen = Mock(side_effect=OSError("network unreachable"))
        wlan.isconnected.side_effect = [False, True]

        with patch.object(frame.time, "sleep"):
            with self.assertRaisesRegex(OSError, "network unreachable"):
                frame._urlopen("http://frame-service/image-metadata.json")

        wlan.disconnect.assert_called_once_with()
        wlan.active.assert_called_once_with(True)
        wlan.connect.assert_called_once_with("test-ssid", "test-password")

    def test_wifi_timeout_does_not_refresh_display(self):
        frame, _, wlan, _ = load_main_module()
        frame.graphics = Mock()
        frame.graphics.measure_text.side_effect = lambda text, scale: len(text) * 10
        frame.WIFI_CONNECT_TIMEOUT_SECONDS = 1
        wlan.isconnected.side_effect = [False, False, True]

        with patch.object(frame.time, "sleep"):
            frame.connect_wifi()

        frame.graphics.update.assert_not_called()
        frame.graphics.text.assert_not_called()

    def test_update_fetches_metadata_and_jpeg(self):
        frame, urequest, _, _ = load_main_module()
        metadata_response = types.SimpleNamespace(
            metadata={"showMetadata": False},
            close=Mock(),
        )
        response = types.SimpleNamespace(close=Mock())
        payload = bytearray(b"\xff\xd8")

        def readinto(buffer):
            if not payload:
                return 0
            buffer[:len(payload)] = payload
            count = len(payload)
            payload.clear()
            return count

        response.readinto = readinto
        urequest.urlopen = Mock(side_effect=[metadata_response, response])

        with patch("builtins.open", mock_open()):
            frame.update()

        self.assertEqual(
            urequest.urlopen.call_args_list,
            [
                call(
                    "http://192.168.1.50:19000/image-metadata.json"
                    "?showMetadata=true"
                ),
                call(
                    "http://192.168.1.50:19000/image.jpg?showMetadata=true"
                ),
            ],
        )
        self.assertIsNone(frame.image_error)
        metadata_response.close.assert_called_once_with()
        response.close.assert_called_once_with()

    def test_fetch_failure_keeps_stage_and_exception_details(self):
        frame, urequest, _, _ = load_main_module()
        frame.MAX_FETCH_ATTEMPTS = 1
        urequest.urlopen = Mock(side_effect=OSError("network unreachable"))

        with (
            patch.object(frame, "connect_wifi"),
            patch("builtins.print"),
        ):
            frame.update()

        self.assertIn("Fetch failed after 1 attempts.", frame.image_error)
        self.assertIn("Metadata request to http://192.168.1.50:19000", frame.image_error)
        self.assertIn("OSError: network unreachable", frame.image_error)

    def test_http_error_includes_status_and_response_body(self):
        frame, _, _, _ = load_main_module()
        error = OSError("request failed")
        error.code = 403
        error.read = Mock(return_value=b'{"error":"Forbidden"}')

        details = frame._format_error("Image request", error)

        self.assertIn("HTTP 403", details)
        self.assertIn('{"error":"Forbidden"}', details)
        error.read.assert_called_once_with(513)

    def test_http_error_response_body_is_bounded(self):
        frame, _, _, _ = load_main_module()
        error = OSError("request failed")
        error.code = 500
        error.read = Mock(return_value=b"x" * 513)

        details = frame._format_error("Image request", error)

        self.assertIn("Response: " + ("x" * 512) + "...", details)

    def test_display_failure_draws_exception_details(self):
        frame, _, _, _ = load_main_module()
        frame.graphics = Mock()
        frame.graphics.measure_text.side_effect = lambda text, scale: len(text) * 10
        jpeg = Mock()
        jpeg.decode.side_effect = RuntimeError("invalid JPEG marker")
        frame.jpegdec.JPEG = Mock(return_value=jpeg)

        frame.draw()

        displayed_text = " ".join(
            call.args[0] for call in frame.graphics.text.call_args_list
        )
        self.assertIn("RuntimeError: invalid JPEG marker", displayed_text)
        self.assertIn("Displaying JPEG immich-image.jpg", displayed_text)
        frame.graphics.rectangle.assert_called_once_with(
            0, 0, frame.WIDTH, frame.HEIGHT
        )

    def test_sleep_disables_wifi_and_uses_frame_rtc(self):
        frame, _, wlan, inky_helper = load_main_module()

        frame.sleep_until_update()

        wlan.disconnect.assert_called_once_with()
        wlan.active.assert_called_once_with(False)
        inky_helper.sleep.assert_called_once_with(frame.UPDATE_INTERVAL)


if __name__ == "__main__":
    unittest.main()
