import unittest

import second_brain


class SmokeTest(unittest.TestCase):
    def test_package_imports_with_version(self):
        self.assertTrue(second_brain.__version__)


if __name__ == "__main__":
    unittest.main()
