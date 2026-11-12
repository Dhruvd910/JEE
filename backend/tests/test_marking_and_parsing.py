"""Marking rules and the official-document parsers."""
import pytest

from app.services import colleges, papers
from app.services.ai import agree, same_answer
from app.services.exams import mark


@pytest.mark.parametrize("resp,marks", [(None, 0), ([], 0), ([1], 4), ([2], -1)])
def test_main_mcq_plus4_minus1(resp, marks):
    assert mark("single", {"sets": [[1]]}, 4, 1, resp) == marks


@pytest.mark.parametrize("resp,marks", [(["7"], 4), ([7.0], 4), ([8], -1), (["x"], -1), (None, 0)])
def test_main_numerical_has_negative_marking_since_2025(resp, marks):
    assert mark("num", {"lo": 7.0, "hi": 7.0}, 4, 1, resp) == marks


def test_advanced_numerical_range_and_no_negative():
    a = {"lo": 1.70, "hi": 1.75}
    assert mark("num", a, 4, 0, [1.72]) == 4
    assert mark("num", a, 4, 0, [1.8]) == 0


@pytest.mark.parametrize("correct,resp,marks", [
    ([0, 1, 2, 3], [0, 1, 2, 3], 4), ([0, 1, 2, 3], [0, 1, 2], 3),  # +3: all four correct, three chosen
    ([0, 1, 3], [0, 1], 2), ([0, 1, 3], [3], 1), ([0, 2], [2], 1),  # +2 / +1 partial
    ([0, 1, 3], [0, 2], -1), ([0, 2], [0, 1, 2], -1),  # any wrong option: negative
])
def test_advanced_multiple_correct_partial_marks(correct, resp, marks):
    assert mark("multi", {"sets": [correct]}, 4, 1, resp) == marks


def test_alternative_keys_take_the_best():
    a = papers.parse_adv_answer("BD or D")
    assert a == {"sets": [[1, 3], [3]]}
    assert mark("multi", a, 4, 1, [3]) == 4 and mark("multi", a, 4, 1, [1]) == 1
    assert mark("single", papers.parse_adv_answer("A or B"), 3, 1, [1]) == 3


@pytest.mark.parametrize("raw,parsed", [
    ("[2520] ", {"lo": 2520.0, "hi": 2520.0}), ("[3.9 TO 4.1]", {"lo": 3.9, "hi": 4.1}),
    ("[0.63 to 0.70]", {"lo": 0.63, "hi": 0.70}), ("ACD", {"sets": [[0, 2, 3]]}), ("Dropped", None)])
def test_parse_adv_answer(raw, parsed):
    assert papers.parse_adv_answer(raw) == parsed


def test_parse_main_paper_and_key():
    text = ("Mathematics Section A\nQuestion Number : 1 Question Id : 111111 Question Type : MCQ Option Shuffling : Yes "
            "Display Question Number : Yes\nOptions :\n900001. \n900002. \n900003. \n900004. \n"
            "Mathematics Section B\nQuestion Number : 2 Question Id : 111112 Question Type : SA Display Question Number : Yes\n")
    rows = papers.parse_main_paper(text)
    assert [(r["qid"], r["type"], r["subject"], r["section"], len(r["option_ids"])) for r in rows] == [
        ("111111", "MCQ", "Mathematics", "Section A", 4), ("111112", "SA", "Mathematics", "Section B", 0)]
    key = papers.parse_main_key("( MATHEMATICS )\n111111 900003\n111112 42\n")
    assert key == {"111111": "900003", "111112": "42"}


def test_parse_advanced_sections():
    block = ("SECTION 1 (Maximum Marks: 12) • This section contains FOUR (04) questions. ONLY ONE of these four options "
             "is the correct answer. Full Marks : +3 If ONLY the correct option is chosen; Negative Marks : −1 In all "
             "other cases. Q.1 x\nSECTION 2 (Maximum Marks: 16) This section contains FOUR (04) questions. ONE OR MORE "
             "THAN ONE of these four option(s) is(are) correct. Full Marks : +4 ONLY if Negative Marks : −2 In all Q.5 y\n"
             "SECTION 3 This section contains TWO (02) question stems. There are TWO (02) questions corresponding to "
             "each question stem. The answer to each question is a NUMERICAL VALUE. Full Marks : +2 If ONLY Q.9 z")
    s = papers.parse_sections(block)
    assert [(x["type"], x["count"], x["full"], x["neg"]) for x in s] == [("single", 4, 3, 1), ("multi", 4, 4, 2), ("num", 4, 2, 0)]


def test_guess_subject():
    assert papers.guess_subject("Q.1 Let f be a function and the matrix A ... tangent to the parabola") == "Mathematics"
    assert papers.guess_subject("Q.1 A particle of mass m moves with velocity v in a magnetic field") == "Physics"
    assert papers.guess_subject("Q.1 The major product of the reaction of the compound with aqueous acid") == "Chemistry"


def test_transcripts_agree_across_notation_but_not_content():
    a = {"stem": "Let z₁, z₂ ∈ ℂ with |z₁|² = 4", "options": ["18", "22", "29", "34"]}
    assert agree(a, {"stem": "Let z_1, z_2 ∈ C with |z_1|^2 = 4", "options": ["18", "22", "29", "34"]})
    assert not agree(a, {"stem": "Let z_1, z_2 ∈ C with |z_1|^2 = 9", "options": ["18", "22", "29", "35"]})
    assert not agree(a, {**a, "needs_figure": True})


def test_variant_answers_compare():
    assert same_answer({"lo": 2.0, "hi": 2.0}, {"lo": 2.005, "hi": 2.005})
    assert not same_answer({"lo": 2.0, "hi": 2.0}, {"lo": 2.5, "hi": 2.5})
    assert same_answer({"sets": [[0, 2]]}, {"sets": [[0, 2]]}) and not same_answer({"sets": [[0]]}, {"sets": [[1]]})


def test_josaa_parsing_and_kinds():
    page = ("<table><tr><th>Institute</th></tr><tr><td>Indian Institute  of Technology Bombay</td><td>CSE</td><td>AI</td>"
            "<td>OPEN</td><td>Gender-Neutral</td><td>1</td><td>66</td></tr>"
            "<tr><td>X</td><td>Y</td><td>AI</td><td>SC (PwD)</td><td>Gender-Neutral</td><td>12P</td><td>15P</td></tr></table>")
    rows = colleges.parse_josaa(page)
    assert rows[0][0] == "Indian Institute of Technology Bombay" and rows[0][6] == "66"
    assert colleges._rank("15P") == (15, True)
    assert [colleges.kind_of(n) for n in ("Indian Institute of Technology (BHU) Varanasi",
                                          "Indian Institute of Information Technology Lucknow",
                                          "Indian Institute of Engineering Science and Technology, Shibpur",
                                          "Birla Institute of Technology, Mesra, Ranchi")] == ["IIT", "IIIT", "NIT", "GFTI"]


def test_nirf_matching_keeps_kinds_and_cities_apart(db):
    from app.models import Institute
    names = ["Indian Institute of Technology Delhi", "National Institute of Technology Delhi",
             "Jawaharlal Nehru University, Delhi", "Indian Institute of Technology (BHU) Varanasi",
             "Indian Institute of Technology Madras"]
    db.add_all([Institute(name=n, kind=colleges.kind_of(n)) for n in names])
    db.commit()
    colleges.attach_nirf(db)
    got = {i.name: i.nirf_rank for i in db.query(Institute)}
    assert got == {"Indian Institute of Technology Delhi": 2, "National Institute of Technology Delhi": 65,
                   "Jawaharlal Nehru University, Delhi": None, "Indian Institute of Technology (BHU) Varanasi": 10,
                   "Indian Institute of Technology Madras": 1}
