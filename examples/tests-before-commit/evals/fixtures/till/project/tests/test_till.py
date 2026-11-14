import unittest

from till import total


class TotalTest(unittest.TestCase):
    def test_total_adds_the_prices(self):
        self.assertEqual(total([250, 120]), 370)


if __name__ == "__main__":
    unittest.main()
