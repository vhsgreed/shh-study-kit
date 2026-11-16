"""Tests for shhkit.search. Offline: builds an index object in memory.

    python3 -m unittest discover -s tests -v
"""
import unittest

from shhkit import search as S


def doc(type_, title, body, date="", ext=""):
    return S._doc(type_, title, body, f"/courses/1/{type_}/{title}", ext=ext, date=date)


IDX = {"course_id": "1", "source": "test", "docs": [
    doc("sida", "Anvisningar och formalia",
        "Det görs en plagiatkontroll av inlämningsuppgiften. OURGINAL är namnet på mjukvaran. "
        "Det är inte tillåtet att använda AI under denna uppgift.", "2026-10-01"),
    doc("sida", "Vetenskaplig artikel",
        "Korta citat skall anges med sidnummer enligt APA. Läs artikeln kritiskt.", "2026-11-10"),
    doc("uppgift", "Examination delfråga: Evidensbaserad vård",
        "Redogör för EBV och vetenskaplig metod. APA-mallen gäller.", "2026-11-26"),
    doc("fil", "Kriterier för komplettering.pdf", "Kriterier för komplettering APA", "2026-09-15", "pdf"),
    doc("anvisning", "Välkomstinformation", "Viktigt att läsa anvisningarna först.", "2026-08-20"),
    doc("diskussion", "Gruppdiskussion metod", "Diskutera begreppet evidens i gruppen.", "2026-11-16"),
]}


def q(raw, **kw):
    return S.run(IDX, raw, **kw)


def titles(res):
    return [h["title"] for h in res["hits"]]


class SearchTests(unittest.TestCase):
    def test_fold_strips_diacritics_and_trims(self):
        self.assertEqual(S.fold("  Omvårdnad   Åt ÅLANDSÖ "), "omvardnad at alandso")

    def test_plain_term_in_body(self):
        res = q("mjukvaran")
        self.assertEqual(res["total"], 1)
        self.assertEqual(titles(res), ["Anvisningar och formalia"])

    def test_implicit_and(self):
        self.assertEqual(q("APA sidnummer")["total"], 1)
        self.assertEqual(q("APA zebra")["total"], 0)

    def test_phrase_order_matters(self):
        self.assertEqual(q('"korta citat"')["total"], 1)
        self.assertEqual(q('"citat korta"')["total"], 0)

    def test_exclusion(self):
        self.assertGreater(q("APA")["total"], q("APA -kriterier")["total"])
        self.assertEqual(q("APA -kriterier")["total"], 2)

    def test_or_dedupes(self):
        res = q('OURGINAL OR "korta citat"')
        self.assertEqual(res["total"], 2)

    def test_type_and_filetype(self):
        self.assertEqual(q("APA type:fil")["total"], 1)
        self.assertEqual(q("APA filetype:pdf")["total"], 1)
        self.assertEqual(q("APA filetype:docx")["total"], 0)

    def test_intitle(self):
        self.assertEqual(q("intitle:vetenskaplig")["total"], 1)

    def test_dates(self):
        self.assertEqual(q("APA after:2026-11-01")["total"], 2)
        self.assertEqual(q("APA before:2026-10-01")["total"], 1)
        self.assertEqual(q("APA after:2026-11-26")["total"], 0)

    def test_unknown_type_is_an_error(self):
        self.assertIn("Okänd typ", q("APA type:kanelbulle")["error"])

    def test_case_sensitivity(self):
        self.assertEqual(q("ourginal")["total"], 1)
        self.assertEqual(q("ourginal", case_sensitive=True)["total"], 0)
        self.assertEqual(q("OURGINAL", case_sensitive=True)["total"], 1)

    def test_short_terms_are_whole_word(self):
        self.assertEqual(q("ai", whole_word=True)["total"], 1)

    def test_fuzzy(self):
        self.assertEqual(q("plagiatkontorl")["total"], 0)
        self.assertEqual(q("plagiatkontorl", fuzzy=True)["total"], 1)
        self.assertEqual(q("korta sitat", fuzzy=True)["total"], 1)

    def test_regex_and_bad_regex(self):
        self.assertEqual(q(r"pla[gy]iat\w*", regex=True)["total"], 1)
        self.assertIn("Ogiltigt", q("[unclosed", regex=True)["error"])

    def test_ranking_is_descending(self):
        hits = q("APA")["hits"]
        self.assertTrue(all(hits[i - 1]["score"] >= hits[i]["score"] for i in range(1, len(hits))))

    def test_suggest(self):
        self.assertEqual(S.suggest(IDX, "OURIGINAL"), "ourginal")
        self.assertEqual(S.suggest(IDX, "plagiatkontorl"), "plagiatkontroll")
        self.assertIsNone(S.suggest(IDX, "OURGINAL"))

    def test_deep_link(self):
        self.assertEqual(S.deep_link("/p/x", "korta citat"), "/p/x#:~:text=korta%20citat")
        self.assertIn("%2D", S.deep_link("/p/x", "a-b"))
        self.assertEqual(S.deep_link("/p/x", ""), "/p/x")

    def test_snippet_contains_match(self):
        d = doc("sida", "T", "x" * 400 + " needle " + "y" * 400)
        self.assertIn("needle", S.snippet(d, 405))

    def test_empty_query(self):
        self.assertTrue(q("   ").get("empty"))


if __name__ == "__main__":
    unittest.main()
