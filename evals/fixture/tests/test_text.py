import unittest

from app.text import normalize_whitespace, title_case


class TextTests(unittest.TestCase):
    def test_normalize_whitespace_collapses_runs(self):
        self.assertEqual(normalize_whitespace("  a \n b  "), "a b")

    def test_title_case_capitalizes_each_word(self):
        self.assertEqual(title_case("hello   world"), "Hello World")


if __name__ == "__main__":
    unittest.main()
