"""Offline tests for the New Quizzes reader. The page text below is copied from a real SHH attempt
screen (2026-11-26); only the parsing and the click planning are tested, no browser."""
from unitoolkit import quizzes

TF = {
    "body": ("Terminologi Innan du svarar ... Frågan på placeringen 1 1 Sant eller falskt 1 poäng "
             "Frågan 1 är inte fäst. Klicka för att fästa. Frågan på placeringen 1 Inom sjukvården är "
             "alla ord och benämningar för sjukdomar eller kroppsdelar ett enhetligt språk Sant Falskt "
             "Nästa fråga Nästa"),
    "inputs": [{"type": "radio", "label": "Sant", "checked": False, "value": ""},
               {"type": "radio", "label": "Falskt", "checked": True, "value": ""}],
    "nav": ["Visa navigeringsmeny för öppen fråga", "Navigera till titel",
            "Navigera till frågan på placeringen 1", "Navigera till frågan på placeringen 2",
            "Navigera till frågan på placeringen 3, obesvarad",
            "Navigera till frågan på placeringen 10, obesvarad"],
}

BLANK = {
    "body": ("Frågan på placeringen 2 2 Fyll i den tomma rutan 1 poäng Frågan 2 är inte fäst. Klicka "
             "för att fästa. Frågan på placeringen 2 \"Kärnkompetensen Fråga tom 1 av 1 är den "
             "kärnkompetens som ligger närmast till hands när man talar om att journalanteckningar "
             "behöver vara entydiga\" Föregående fråga Föregående Nästa fråga Nästa"),
    "inputs": [{"type": "text", "label": "Fråga tom 1 av 1", "checked": False, "value": "säker vård"}],
}

MULTI_OPTS = [{"label": "betydelsetunga begrepp", "checked": True},
              {"label": "kopplar begrepp till ett specifikt område", "checked": False},
              {"label": "resultat av ett målinriktat arbete med termer", "checked": False},
              {"label": "bildningen av ord, hur de struktureras och utvecklas", "checked": True}]


def test_parse_true_false():
    q = quizzes.parse(TF)
    assert q["position"] == 1 and q["type"] == "true/false"
    assert q["stem"].startswith("Inom sjukvården") and q["stem"].endswith("enhetligt språk")
    assert q["options"] == [{"label": "Sant", "checked": False}, {"label": "Falskt", "checked": True}]
    assert q["of"] == 10 and q["unanswered"] == [3, 10]


def test_parse_blank_marks_the_gap_and_reads_value():
    q = quizzes.parse(BLANK)
    assert q["position"] == 2 and q["type"] == "fill in the blank"
    assert "Kärnkompetensen ___ är den kärnkompetens" in q["stem"]
    assert q["blanks"] == ["säker vård"] and q["options"] == []


def test_parse_no_question_on_screen():
    q = quizzes.parse({"body": "Försökshistorik Försök 1 Försök 2 Sätt igång", "inputs": []})
    assert q["position"] is None and "no question" in q["note"]


def test_plan_multi_sets_exact_set():
    # want the first three; the fourth is wrongly ticked and must be unticked
    clicks, err = quizzes.plan(MULTI_OPTS, ["betydelsetunga", "kopplar begrepp", "målinriktat"], multi=True)
    assert err is None
    assert clicks == ["kopplar begrepp till ett specifikt område",
                      "resultat av ett målinriktat arbete med termer",
                      "bildningen av ord, hur de struktureras och utvecklas"]


def test_plan_single_refuses_ambiguous_and_unknown():
    opts = [{"label": "Sant", "checked": False}, {"label": "Falskt", "checked": False}]
    assert quizzes.plan(opts, ["a"], multi=False)[1]            # matches both: refuse
    assert "no option matches" in (quizzes.plan(opts, ["kanske"], multi=False)[1] or "")
    assert quizzes.plan(opts, ["falskt"], multi=False) == (["Falskt"], None)


def test_plan_noop_when_already_right():
    opts = [{"label": "Sant", "checked": False}, {"label": "Falskt", "checked": True}]
    assert quizzes.plan(opts, ["Falskt"], multi=False) == ([], None)


def test_read_js_has_no_python_comments():
    # a '#' pasted into a JS string is the classic silent syntax error
    assert "#" not in quizzes.READ_JS
