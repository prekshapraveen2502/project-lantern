"""
Part 0 tests: choosing the earnings release in an Item 2.02 8-K. No network: each test passes
documents shaped like edgar.sgml_documents() output.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest.earnings import pick_release  # noqa: E402


def doc(typ, filename):
    return {"type": typ, "filename": filename, "text": f"<html>{filename}</html>"}


MAIN = doc("8-K", "four-20260806.htm")


def test_ex99_1_wins_over_ex99(capsys):
    main, release, typ = pick_release([MAIN, doc("EX-99", "other.htm"), doc("EX-99.1", "ex991q1.htm")], "T 1")
    assert (main["filename"], release["filename"], typ) == ("four-20260806.htm", "ex991q1.htm", "EX-99.1")
    assert "[WARN]" not in capsys.readouterr().out


def test_single_ex99_is_the_fallback(capsys):
    """Shift4 0001794669-26-000042 tagged its release EX-99 (file ex991q2.htm)."""
    _, release, typ = pick_release([MAIN, doc("EX-99", "ex991q2.htm"), doc("GRAPHIC", "ex991q2001.jpg")], "FOUR 1")
    assert (release["filename"], typ) == ("ex991q2.htm", "EX-99")
    assert "[WARN] FOUR 1: no EX-99.1 exhibit; using the only EX-99 document ex991q2.htm" in capsys.readouterr().out


def test_two_ex99_documents_fail():
    with pytest.raises(RuntimeError, match="2 EX-99 documents"):
        pick_release([MAIN, doc("EX-99", "a.htm"), doc("EX-99", "b.htm")], "T 2")


def test_no_release_fails():
    with pytest.raises(RuntimeError, match="0 EX-99 documents"):
        pick_release([MAIN, doc("EX-99.2", "ex992.htm")], "T 3")


def test_missing_8k_document_fails():
    with pytest.raises(RuntimeError, match="missing the 8-K document"):
        pick_release([doc("EX-99.1", "ex991.htm")], "T 4")
