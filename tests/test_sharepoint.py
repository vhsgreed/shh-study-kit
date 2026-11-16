"""Tests for unistoolkit.sharepoint classification and link scanning.

No network and no browser: only the parsing rules, which is where the bugs were.
"""
import unittest

from unitoolkit import docs as D
from unitoolkit import sharepoint as SP


class SniffTests(unittest.TestCase):
    def test_pdf_and_zip(self):
        self.assertEqual(SP.sniff(b"%PDF-1.7 ..."), "pdf")
        self.assertEqual(SP.sniff(b"PK\x03\x04"), "docx")

    def test_html_with_utf8_bom_is_html(self):
        # a real share returned "\xef\xbb\xbf<%@ Page ..." and was classified as binary
        self.assertEqual(SP.sniff(b"\xef\xbb\xbf<%@ Page language=\"C#\" %>"), "html")

    def test_unknown_is_bin(self):
        self.assertEqual(SP.sniff(b"MZ\x90\x00"), "bin")

    def test_name_from_content_disposition(self):
        cd = ("attachment;filename*=utf-8''Student%C3%A4renden%281%29%2Easpx;"
              'filename="Student%C3%A4renden(1).aspx"')
        self.assertEqual(SP.name_from_cd(cd), "Studentärenden(1).aspx")
        self.assertEqual(SP.name_from_cd('attachment; filename="Rapport 2026.pdf"'), "Rapport 2026.pdf")
        self.assertIsNone(SP.name_from_cd(None))
        self.assertIsNone(SP.name_from_cd("inline"))

    def test_share_to_download(self):
        url, origin = SP.share_to_download(
            "https://shh365.sharepoint.com/:u:/s/Minasidor-Student/IQABC?e=xyz")
        self.assertEqual(origin, "https://shh365.sharepoint.com/sites/Minasidor-Student/")
        self.assertIn("_layouts/15/download.aspx?share=IQABC", url)
        self.assertEqual(SP.share_to_download("https://example.com/x"), (None, None))


class LinkScanTests(unittest.TestCase):
    def test_links_are_classified(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            pages = pathlib.Path(td) / "2416" / "pages"
            pages.mkdir(parents=True)
            (pages / "p1.html").write_text(
                '<a href="https://shh.instructure.com/courses/2416/files/455267?wrap=1">f</a>'
                '<a href="https://shh365.sharepoint.com/:u:/s/Minasidor-Student/IQAB?e=1">s</a>'
                '<a href="https://example.org/page">o</a>'
                '<a href="https://kaf-canvas-328.kaltura.nordu.net/x">v</a>'
                '<a href="#top">t</a>')
            links = D.page_links(2416, mirror_root=pathlib.Path(td) / "2416")
        kinds = {(l["kind"], l["url"].split("/")[2]) for l in links}
        self.assertIn(("canvas_file", "shh.instructure.com"), kinds)
        self.assertIn(("share", "shh365.sharepoint.com"), kinds)
        self.assertIn(("other", "example.org"), kinds)
        self.assertIn(("video", "kaf-canvas-328.kaltura.nordu.net"), kinds)
        self.assertFalse(any(l["url"].startswith("#") for l in links), "anchors are not links")

    def test_missing_mirror_returns_nothing(self):
        self.assertEqual(D.page_links(999, mirror_root="/nonexistent"), [])


if __name__ == "__main__":
    unittest.main()
