import json
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from job_radar.connectors import common


class ByteResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ReadTimeoutResponse(ByteResponse):
    def read(self):
        raise TimeoutError("body read timed out")


def http_error(status, headers=None):
    return HTTPError(
        "https://example.invalid/jobs",
        status,
        "request failed",
        headers or {},
        None,
    )


class HttpRetryTests(unittest.TestCase):
    def setUp(self):
        sleep_patcher = patch.object(common.time, "sleep")
        jitter_patcher = patch.object(
            common.random,
            "uniform",
            return_value=0.0,
        )
        log_patcher = patch.object(common.LOGGER, "warning")
        self.sleep = sleep_patcher.start()
        self.jitter = jitter_patcher.start()
        self.warning = log_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        self.addCleanup(jitter_patcher.stop)
        self.addCleanup(log_patcher.stop)

    def http_error(self, status, headers=None):
        error = http_error(status, headers)
        self.addCleanup(error.close)
        return error

    def test_success_on_first_attempt(self):
        operation = Mock(return_value="ok")

        result = common.request_with_retry(operation)

        self.assertEqual(result, "ok")
        operation.assert_called_once_with()
        self.sleep.assert_not_called()
        self.warning.assert_not_called()

    def test_timeout_then_success(self):
        operation = Mock(side_effect=[TimeoutError("slow"), "ok"])

        result = common.request_with_retry(
            operation,
            context="company='Example' source=test",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        self.sleep.assert_called_once_with(0.5)
        self.assertIn("attempt=%s", self.warning.call_args.args[0])

    @patch("job_radar.connectors.common.urlopen")
    def test_timeout_while_reading_body_then_success(self, urlopen):
        urlopen.side_effect = [
            ReadTimeoutResponse(b""),
            ByteResponse(b"ok"),
        ]

        result = common.fetch_text("https://example.invalid/jobs")

        self.assertEqual(result, "ok")
        self.assertEqual(urlopen.call_count, 2)
        self.sleep.assert_called_once_with(0.5)

    def test_503_then_success(self):
        operation = Mock(side_effect=[self.http_error(503), "ok"])

        result = common.request_with_retry(operation)

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        self.sleep.assert_called_once_with(0.5)

    def test_429_respects_retry_after(self):
        operation = Mock(
            side_effect=[
                self.http_error(429, {"Retry-After": "3"}),
                "ok",
            ],
        )

        result = common.request_with_retry(operation)

        self.assertEqual(result, "ok")
        self.sleep.assert_called_once_with(3.0)

    def test_exhaustion_raises_after_maximum_number_of_calls(self):
        error = TimeoutError("still slow")
        operation = Mock(side_effect=error)

        with self.assertRaises(TimeoutError) as raised:
            common.request_with_retry(operation)

        self.assertIs(raised.exception, error)
        self.assertEqual(operation.call_count, common.MAX_HTTP_ATTEMPTS)
        self.assertEqual(
            [call.args[0] for call in self.sleep.call_args_list],
            [0.5, 1.0],
        )

    def test_404_is_not_retried(self):
        operation = Mock(side_effect=self.http_error(404))

        with self.assertRaises(HTTPError):
            common.request_with_retry(operation)

        operation.assert_called_once_with()
        self.sleep.assert_not_called()

    @patch("job_radar.connectors.common.urlopen")
    def test_json_parsing_error_is_not_retried(self, urlopen):
        urlopen.return_value = ByteResponse(b"not-json")

        with self.assertRaises(json.JSONDecodeError):
            common.fetch_json("https://example.invalid/jobs")

        urlopen.assert_called_once()
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
