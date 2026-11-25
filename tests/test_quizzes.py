"""Offline tests for the New Quizzes reader: only the shaping, no browser."""
from unitoolkit import quizzes


RAW = {
    'progress': 'Fråga 3 av 10',
    'radios': [
        {'label': 'Sant', 'checked': False, 'question': 'Termer är statiska'},
        {'label': 'Falskt', 'checked': True, 'question': 'Termer är statiska'},
    ],
    'checks': [
        {'label': 'betydelsetunga begrepp', 'checked': True, 'question': 'Ordet terminologi har fler betydelser'},
        {'label': 'bildningen av ord', 'checked': False, 'question': 'Ordet terminologi har fler betydelser'},
    ],
    'fields': [
        {'kind': 'input', 'value': '', 'placeholder': 'Ditt svar', 'question': 'Det är ___ som ansvarar'},
    ],
    'editables': [],
    'buttons': [{'text': 'Föregående', 'disabled': False}, {'text': 'Skicka in', 'disabled': True}],
    'bodyText': 'Fråga 3 av 10 Termer är statiska',
}


def test_shape_groups_questions_and_reads_state():
    out = quizzes.shape(RAW)
    assert out['progress'] == 'Fråga 3 av 10'
    q1 = out['questions']['Termer är statiska']
    assert q1['type'] == 'single choice' and q1['checked'] == 'Falskt'
    q2 = out['questions']['Ordet terminologi har fler betydelser']
    assert q2['type'] == 'multiple choice' and q2['checked'] == ['betydelsetunga begrepp']
    q3 = out['questions']['Det är ___ som ansvarar']
    assert q3['type'] == 'text' and q3['placeholder'] == 'Ditt svar'
    # a disabled button is not offered as an action
    assert out['buttons'] == ['Föregående']


def test_shape_survives_empty_page():
    out = quizzes.shape({})
    assert out['questions'] == {} and out['buttons'] == []


def test_scan_js_has_no_python_comments():
    # a '#' pasted into a JS string is the classic silent syntax error
    for name in ('SCAN_JS', 'SET_CHOICE_JS', 'SET_TEXT_JS', 'SET_EDITABLE_JS', 'CLICK_BUTTON_JS'):
        js = getattr(quizzes, name)
        assert '#' not in js, name
