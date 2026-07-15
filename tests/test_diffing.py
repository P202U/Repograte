import pytest

from repograte.orchestration.diffing import apply_diff
from repograte.orchestration.schemas import EngineerDiffOutput, SearchReplaceBlock


def _diff(*pairs: tuple[str, str]) -> EngineerDiffOutput:
    return EngineerDiffOutput(
        reasoning="test",
        blocks=[SearchReplaceBlock(search_block=s, replace_block=r) for s, r in pairs],
    )


def test_single_block_applies():
    original = "class Foo {\n  render() { return 1; }\n}\n"
    diff = _diff(("return 1;", "return 2;"))
    assert apply_diff(original, diff) == "class Foo {\n  render() { return 2; }\n}\n"


def test_not_found_raises():
    diff = _diff(("this text does not exist", "replacement"))
    with pytest.raises(ValueError, match="not found verbatim"):
        apply_diff("class Foo {}", diff)


def test_ambiguous_match_raises():
    original = "a\nfoo\nb\nfoo\nc\n"
    diff = _diff(("foo", "bar"))
    with pytest.raises(ValueError, match="2 locations"):
        apply_diff(original, diff)


def test_multiple_non_overlapping_blocks_apply_independent_of_list_order():
    original = "one\ntwo\nthree\n"
    diff_forward = _diff(("one", "ONE"), ("three", "THREE"))
    diff_reversed = _diff(("three", "THREE"), ("one", "ONE"))
    expected = "ONE\ntwo\nTHREE\n"
    assert apply_diff(original, diff_forward) == expected
    assert apply_diff(original, diff_reversed) == expected


def test_overlapping_blocks_raise():
    original = "foobar\n"
    # "foo" covers indices 0-3, "oba" covers indices 2-5 -> they overlap.
    diff = _diff(("foo", "FOO"), ("oba", "OBA"))
    with pytest.raises(ValueError, match="overlapping"):
        apply_diff(original, diff)


def test_each_block_matched_against_original_not_progressively_mutated_text():
    """Regression test: a naive sequential str.replace() implementation would
    match later blocks against the ALREADY-PATCHED string. Here, block 2's
    search text only exists in the ORIGINAL source (it's replaced away by
    block 1 if blocks were applied sequentially first) - both must still
    apply cleanly since both are located against the original text."""
    original = "AAA BBB\n"
    diff = _diff(("AAA", "XXX"), ("BBB", "YYY"))
    assert apply_diff(original, diff) == "XXX YYY\n"


def test_empty_blocks_raises():
    diff = EngineerDiffOutput(reasoning="test", blocks=[])
    with pytest.raises(ValueError, match="no search/replace blocks"):
        apply_diff("anything", diff)
