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

    def test_anchor_never_starts_mid_word(self):
        # the real failure: "#:~:text=ontrol (OURGINAL)Observera att ..." -> Chrome ignored it
        body = ("Information om plagiatkontrol (OURGINAL) Observera att OURGINAL, en automatisk "
                "mjukvara för textmatchning, används vid bedömning av svar.")
        anchor = S.anchor_for("Anvisningar", body.index("ontrol"), body)
        self.assertTrue(anchor, "an anchor is produced")
        self.assertFalse(anchor.startswith("ontrol"), anchor)
        self.assertIn("plagiatkontrol", anchor)
        self.assertNotIn("(OURGINAL)Observera", anchor, "must not glue two blocks together")
        self.assertIn(anchor, body)

    def test_anchor_is_whole_words_and_length_bounded(self):
        d = doc("sida", "T", "alfa beta gamma delta epsilon zeta eta theta iota kappa", "2026-01-01")
        a = S.anchor_for("T", 5, d["body"])
        self.assertTrue(a)
        self.assertGreaterEqual(len(a.split()), 3, a)
        self.assertLessEqual(len(a), 120, a)

    def test_unverifiable_anchor_degrades_to_plain_link(self):
        self.assertEqual(S.deep_link("/p/x", ""), "/p/x")
        self.assertFalse(S.fragment_matches("", "t", "b"))
        self.assertFalse(S.fragment_matches("zzz not here at all", "t", "b"))
        self.assertEqual(S.anchor_for("t", 999, "short"), "")

    def test_anchor_never_spans_two_blocks(self):
        # second real failure: "</p><p>" was glued into "(OURGINAL)Observera"
        body = ("Information om plagiatkontrol (OURGINAL)\n"
                "Observera att OURGINAL, en automatisk mjukvara för textmatchning, används.")
        d = doc("sida", "T", body, "2026-01-01")
        hit = S.run({"course_id": "1", "source": "t", "docs": [d]}, "OURGINAL")["hits"][0]
        a = S.anchor_target(hit, "OURGINAL")
        self.assertTrue(a)
        self.assertNotIn("\n", a, a)
        self.assertTrue(any(a in line for line in body.split("\n")), f"spans blocks: {a}")

    def test_strip_html_keeps_block_boundaries(self):
        out = S.strip_html("<p>alfa beta</p><p>gamma delta</p>")
        self.assertEqual(out, "alfa beta\ngamma delta")
        self.assertNotIn("gamma", S.strip_html("<p>alfa beta</p>") + "x")

    def test_anchor_contains_the_search_term(self):
        # regression: the anchor scrolled near the match but contained none of the query
        for kw, query in (({}, "OURGINAL"), ({"fuzzy": True}, "OURIGINAL")):
            hit = S.run(IDX, query, **kw)["hits"][0]
            self.assertIn("ourginal", S.fold(hit["anchor"]), f"{query} -> {hit['anchor']}")

    def test_result_urls_carry_a_verified_anchor(self):
        res = S.run(IDX, "OURGINAL")
        hit = res["hits"][0]
        self.assertTrue(hit["anchor"])
        self.assertIn(hit["anchor"], hit["body"])
        self.assertIn("#:~:text=", hit["url"])
        enc = hit["url"].split("text=", 1)[1]
        import urllib.parse
        self.assertEqual(urllib.parse.unquote(enc), hit["anchor"])

    def test_fuzzy_and_regex_urls_are_anchored_too(self):
        for kw in ({"fuzzy": True}, {"regex": True}):
            query = "OURIGINAL" if kw.get("fuzzy") else "OURG\\w*"
            res = S.run(IDX, query, **kw)
            self.assertTrue(res["hits"], query)
            hit = res["hits"][0]
            self.assertTrue(hit["anchor"], query)
            self.assertIn(hit["anchor"], hit["body"], query)

    def test_snippet_contains_match(self):
        d = doc("sida", "T", "x" * 400 + " needle " + "y" * 400)
        self.assertIn("needle", S.snippet(d, 405))

    def test_empty_query(self):
        self.assertTrue(q("   ").get("empty"))


if __name__ == "__main__":
    unittest.main()
