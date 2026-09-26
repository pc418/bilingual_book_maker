"""`suspected_echo`: an identical translation, flagged only when it matters.

PIN (lead 260925 with astra consult, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md,
section "T3, open"; rescoped the same day to detection only, True/False):
an identical reply is flagged only when it is prose (at least
MIN_PROSE_LETTERS letters, not mostly capitalised words) written mostly
(MIN_FOREIGN_SHARE) in a script the target language does not use. A
same-script echo, a name, protected content, or a target whose script is
unknown is never flagged. A script heuristic, not language identification.
"""

import unicodedata

import pytest

from book_maker.translation_checks import (
    MIN_PROSE_LETTERS,
    suspected_echo,
    target_scripts,
)

# One of the three quotations the tester's hekate run got back unchanged.
GREEK = "Πατήρ ού φόβον ένθρώσκει, Πειθώ δ' έπιχεύει."


def test_a_greek_quotation_echoed_to_simplified_chinese_is_flagged():
    assert suspected_echo(GREEK, GREEK, "zh-hans")


def test_the_target_may_be_given_by_its_name():
    assert suspected_echo(GREEK, GREEK, "simplified chinese")


def test_a_reply_that_differs_is_not_an_echo():
    assert not suspected_echo(
        GREEK, "父神并非以恐惧强行灌入，而是倾注劝诱。", "zh-hans"
    )


def test_a_source_already_in_the_target_script_is_not_flagged():
    chinese = "父神并非以恐惧强行灌入，而是倾注劝诱，这是神谕中反复出现的说法。"
    assert not suspected_echo(chinese, chinese, "zh-hans")


def test_a_same_script_echo_is_not_flagged():
    french = "Le Père ne jette pas la crainte, mais il verse la persuasion."
    assert not suspected_echo(french, french, "fr")
    english = "The Father does not thrust in fear, but rather pours in Persuasion."
    assert not suspected_echo(english, english, "fr")


def test_an_english_sentence_echoed_to_chinese_is_flagged():
    english = "The Father does not thrust in fear, but rather pours in persuasion."
    assert suspected_echo(english, english, "zh-hans")


@pytest.mark.parametrize("name", ["Plato", "ΣΩΚΡΑΤΗΣ", "Proclus and Psellus"])
def test_names_and_short_phrases_say_nothing(name):
    assert not suspected_echo(name, name, "zh-hans")


def test_a_title_cased_line_of_names_says_nothing():
    names = "Plato Aristotle Socrates Iamblichus"
    assert len([c for c in names if c.isalpha()]) >= MIN_PROSE_LETTERS
    assert not suspected_echo(names, names, "zh-hans")


@pytest.mark.parametrize(
    "protected",
    [
        "`print_the_whole_thing(argument_value)`",
        "⟦code1⟧ ⟦em2⟧ ⟦link3⟧",
        "@@BBM_MD_PROTECT_0@@ @@BBM_MD_PROTECT_1@@",
        "https://example.com/some/long/path/to/a/resource",
        "$\\alpha + \\beta = \\gamma_{index}$",
    ],
)
def test_protected_content_alone_says_nothing(protected):
    assert not suspected_echo(protected, protected, "zh-hans")


def test_protected_content_does_not_count_towards_the_letter_floor():
    # Ten Greek letters (under the floor) beside a long URL: the URL's
    # letters must not lift it over.
    short = "Πειθώ φόβον https://example.com/persuasion/and/fear/in/the/oracles"
    assert not suspected_echo(short, short, "zh-hans")


def test_whitespace_and_nfc_variants_still_count_as_identical():
    decomposed = unicodedata.normalize("NFD", GREEK)
    assert decomposed != GREEK
    spaced = "  " + GREEK.replace(" ", " \n  ") + "\n"
    assert suspected_echo(GREEK, decomposed, "zh-hans")
    assert suspected_echo(spaced, GREEK, "zh-hans")


def test_a_target_whose_script_is_unknown_is_never_flagged():
    assert target_scripts("Klingon") is None
    assert not suspected_echo(GREEK, GREEK, "Klingon")
    assert not suspected_echo(GREEK, GREEK, "")
    assert not suspected_echo(GREEK, GREEK, None)


def test_a_greek_target_does_not_flag_greek():
    assert not suspected_echo(GREEK, GREEK, "el")
