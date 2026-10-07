import unittest
from unittest import mock

from second_brain.config import ConfigError
from second_brain.notify import notify


class NotifyTest(unittest.TestCase):
    """Timer jobs have nobody watching, and a notifier that raises buries the
    thing it was reporting on."""

    def settings(self):
        return mock.Mock(telegram_bot_token="t", telegram_allowed_user_id=42)

    def test_a_message_is_sent(self):
        with mock.patch("second_brain.notify._send") as send:
            sent = notify("backup failed", settings=self.settings())
        self.assertTrue(sent)
        self.assertEqual(send.call_args.args[1], "backup failed")

    def test_blank_messages_are_not_sent(self):
        with mock.patch("second_brain.notify._send") as send:
            self.assertFalse(notify("   ", settings=self.settings()))
        send.assert_not_called()

    def test_a_telegram_failure_is_swallowed_and_logged(self):
        async def boom(settings, text):
            raise RuntimeError("Bad Gateway")

        with mock.patch("second_brain.notify._send", boom):
            with self.assertLogs("second_brain.notify", level="WARNING") as logged:
                sent = notify("something", settings=self.settings())

        self.assertFalse(sent)
        self.assertIn("Bad Gateway", logged.output[0])

    def test_missing_configuration_is_not_an_exception(self):
        with mock.patch(
            "second_brain.notify.Settings.from_env", side_effect=ConfigError("no token")
        ):
            with self.assertLogs("second_brain.notify", level="WARNING"):
                self.assertFalse(notify("something"))


if __name__ == "__main__":
    unittest.main()
