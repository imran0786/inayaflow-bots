import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check


class T(unittest.TestCase):
    def test_followed(self):
        self.assertEqual(check.analyse('<a href="https://inayaflow.ai/">x</a>')[0], "followed")

    def test_nofollow(self):
        self.assertEqual(check.analyse('<a rel="nofollow noopener" href="https://www.inayaflow.ai/tools">x</a>')[0], "nofollow")

    def test_followed_beats_nofollow(self):
        h = '<a rel="nofollow" href="https://inayaflow.ai">a</a><a href="https://inayaflow.ai/tools">b</a>'
        self.assertEqual(check.analyse(h)[0], "followed")

    def test_ugc_sponsored(self):
        self.assertEqual(check.analyse('<a rel="ugc nofollow" href="https://inayaflow.ai">a</a>')[0], "ugc")
        self.assertEqual(check.analyse('<a rel="sponsored" href="https://inayaflow.ai">a</a>')[0], "sponsored")

    def test_none_and_lookalike(self):
        self.assertIsNone(check.analyse('<a href="https://notinayaflow.ai">a</a><a href="/x">b</a>')[0])

class OurPage(unittest.TestCase):
    def test_page_the_link_points_to(self):
        pg = lambda h: check.our_page(check.analyse(h)[1])
        self.assertEqual(pg('<a href="https://inayaflow.ai/">x</a>'), "/")
        self.assertEqual(pg('<a href="https://www.inayaflow.ai/tools/tiktok-money-calculator/">x</a>'), "/tools/tiktok-money-calculator")
        self.assertEqual(pg('<a rel="nofollow" href="https://inayaflow.ai/">a</a><a href="https://inayaflow.ai/blog/x">b</a>'), "/blog/x")  # the followed one counts
        self.assertEqual(pg('<a href="https://other.com/">a</a>'), "")


if __name__ == "__main__":
    unittest.main()
