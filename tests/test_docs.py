"""Tests for unistudy.docs: extraction, page numbers, cache keys, OCR decisions.

No network: fixtures are built in memory. Real PDFs are exercised by the `docs` tool run.

    python3 -m unittest discover -s tests -p 'test_docs.py'
"""
import io
import zipfile

import unittest

from unistudy import docs as D


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


DOCX = make_zip({
    "word/document.xml":
        '<w:document><w:body>'
        '<w:p><w:r><w:t>Syftet med denna delfråga</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Antal ord: 900</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Rubrik</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>',
})

PPTX = make_zip({
    "ppt/slides/slide1.xml":
        '<p:sld><a:p><a:r><a:t>Personcentrerad vård</a:t></a:r></a:p></p:sld>',
    "ppt/slides/slide2.xml":
        '<p:sld><a:p><a:r><a:t>Kärnkompetenser</a:t></a:r></a:p></p:sld>',
})

XLSX = make_zip({"xl/sharedStrings.xml": '<sst><si><t>Läkemedel</t></si><si><t>Dos</t></si></sst>'})


class ExtractTests(unittest.TestCase):
    def test_docx_keeps_paragraphs_and_tables(self):
        text = D.docx_text(DOCX)
        self.assertIn("Syftet med denna delfråga", text)
        self.assertIn("Antal ord: 900", text)
        self.assertIn("Rubrik", text)
        self.assertIn("\n", text, "paragraph boundaries must survive")

    def test_pptx_labels_slides_in_order(self):
        text = D.pptx_text(PPTX)
        self.assertTrue(text.startswith("Bild 1:"), text)
        self.assertIn("Personcentrerad vård", text)
        self.assertIn("Bild 2:", text, "slide order and numbering")

    def test_xlsx_reads_shared_strings(self):
        self.assertIn("Läkemedel", D.xlsx_text(XLSX))

    def test_html_text_keeps_block_structure(self):
        text = D.html_text(b"<p>alfa</p><p>beta</p><script>x()</script>")
        self.assertEqual(text, "alfa\nbeta")

    def test_extract_dispatches_and_reports_kind(self):
        info = D.extract("Skrivmall.docx", DOCX)
        self.assertEqual(info["kind"], "docx")
        self.assertIn("Antal ord", info["text"])
        self.assertFalse(info["needs_ocr"])

    def test_extract_marks_image_only_content(self):
        info = D.extract("scan.pdf", b"%PDF-1.4 not a real pdf", allow_ocr=False)
        self.assertTrue(info["needs_ocr"], "an unreadable pdf must be flagged for OCR")
        self.assertEqual(info["ocr_pages"], 0, "no OCR unless asked")

    def test_extract_unknown_binary_is_labelled(self):
        info = D.extract("thing.exe", b"MZ\x90\x00")
        self.assertEqual(info["kind"], "binary")
        self.assertEqual(info["text"], "")

    def test_extract_plain_text_file(self):
        info = D.extract("notes.md", "Rad ett\nRad två".encode())
        self.assertIn("Rad två", info["text"])


class PageNumberTests(unittest.TestCase):
    def test_page_of_uses_form_feeds(self):
        text = "sida1 text\fsida2 text\fsida3 text"
        self.assertEqual(D.page_of(text, 0), 1)
        self.assertEqual(D.page_of(text, text.index("sida2")), 2)
        self.assertEqual(D.page_of(text, text.index("sida3")), 3)
        self.assertIsNone(D.page_of("", 0))


class CacheTests(unittest.TestCase):
    def test_cache_key_changes_with_updated_at(self):
        a = D.cache_key({"id": 5, "updated_at": "2026-11-01T10:00:00Z"})
        b = D.cache_key({"id": 5, "updated_at": "2026-11-02T10:00:00Z"})
        c = D.cache_key({"id": 6, "updated_at": "2026-11-01T10:00:00Z"})
        self.assertNotEqual(a, b, "an updated file must be re-read")
        self.assertNotEqual(a, c)
        self.assertEqual(a, D.cache_key({"id": 5, "updated_at": "2026-11-01T10:00:00Z"}))

    def test_cache_key_falls_back_to_created_at(self):
        self.assertEqual(D.cache_key({"id": 5, "created_at": "2026-01-01"}),
                         D.cache_key({"id": 5, "created_at": "2026-01-01"}))


if __name__ == "__main__":
    unittest.main()
