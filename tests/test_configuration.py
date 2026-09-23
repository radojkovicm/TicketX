import os
import unittest
from unittest.mock import patch

import auth


class EnvironmentConfigurationTests(unittest.TestCase):
    def test_session_timeout_uses_default_for_blank_or_invalid_values(self):
        for value in ("", "not-a-number", "0", "-10"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"SESSION_TIMEOUT_SECONDS": value}, clear=False):
                    self.assertEqual(
                        auth._get_positive_int_env("SESSION_TIMEOUT_SECONDS", 3600),
                        3600,
                    )

    def test_session_timeout_accepts_positive_integer(self):
        with patch.dict(os.environ, {"SESSION_TIMEOUT_SECONDS": "7200"}, clear=False):
            self.assertEqual(
                auth._get_positive_int_env("SESSION_TIMEOUT_SECONDS", 3600),
                7200,
            )


if __name__ == "__main__":
    unittest.main()
