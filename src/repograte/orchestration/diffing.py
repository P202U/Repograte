from .schemas import EngineerDiffOutput


def apply_diff(original_code: str, diff: EngineerDiffOutput) -> str:
    """Apply every search/replace block in ``diff`` to ``original_code``.

    Raises:
        ValueError: if any ``search_block`` doesn't match exactly once in
            ``original_code``, or if two blocks target overlapping regions.
    """
    if not diff.blocks:
        raise ValueError("Diff contains no search/replace blocks to apply.")

    spans: list[tuple[int, int, str, int]] = (
        []
    )  # (start, end, replace_text, block_index)
    for i, block in enumerate(diff.blocks):
        occurrences = original_code.count(block.search_block)
        if occurrences == 0:
            raise ValueError(
                f"Block {i}: search_block not found verbatim in the source file. "
                "The Engineer must copy the existing code exactly, including whitespace."
            )
        if occurrences > 1:
            raise ValueError(
                f"Block {i}: search_block matches {occurrences} locations in the source; "
                "it must uniquely identify a single location. Include more surrounding context."
            )
        start = original_code.index(block.search_block)
        end = start + len(block.search_block)
        spans.append((start, end, block.replace_block, i))

    # Guard against overlapping edits, which would make the result ambiguous and order-dependent.
    by_position = sorted(spans, key=lambda s: s[0])
    for (a_start, a_end, _, a_i), (b_start, b_end, _, b_i) in zip(
        by_position, by_position[1:]
    ):
        if b_start < a_end:
            raise ValueError(
                f"Blocks {a_i} and {b_i} target overlapping regions of the source; "
                "each search_block must cover a disjoint region."
            )

    # Splice back-to-front so earlier offsets remain valid as we go.
    patched = original_code
    for start, end, replace_text, _ in sorted(spans, key=lambda s: s[0], reverse=True):
        patched = patched[:start] + replace_text + patched[end:]
    return patched
