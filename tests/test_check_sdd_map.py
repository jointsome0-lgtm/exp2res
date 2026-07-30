from scripts import check_sdd_map


def test_section_heading_ignores_shorter_fence_example_inside_longer_fence() -> None:
    body = """\
## §99. Wrong section
````markdown
```
## §30. Example inside code
```
````
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_section_heading_ignores_mismatched_closing_fence() -> None:
    body = """\
## §99. Wrong section
~~~~
```
## §30. Example inside code
~~~~
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_fence_with_info_string_does_not_close_open_fence() -> None:
    body = """\
## §99. Wrong section
```
```python
## §30. Example inside code
```
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_section_heading_is_found_after_compatible_closing_fence() -> None:
    body = """\
```python
## §30. Example inside code
````
## §30. Real section
"""

    assert check_sdd_map.has_section_heading(body, 30)


def test_backtick_in_info_string_does_not_open_fence() -> None:
    body = """\
```not`a-fence
## §30. Real section
```
"""

    assert check_sdd_map.has_section_heading(body, 30)


def test_section_heading_ignores_html_comments() -> None:
    body = """\
## §99. Wrong section
<!--
## §30. Example inside comment
-->
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_section_heading_rejects_foreign_root_heading() -> None:
    body = """\
## §30. Real section
 ## §31. Foreign section
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_index_heading_inside_fence_is_ignored(tmp_path, monkeypatch) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
```
## § Index

- §0 fenced route
- Decision Log — fenced route
```
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == []
    assert errors == [
        "SDD.md must contain exactly one canonical '## § Index' heading "
        "and no variant spellings, found 0 candidate(s)"
    ]


def test_inline_comment_literal_does_not_hide_foreign_heading() -> None:
    body = """\
## §30. Real section
`<!--`
## §31. Foreign section
`-->`
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_numbered_non_regular_spec_paths_are_rejected(tmp_path, monkeypatch) -> None:
    spec_directory = tmp_path / "spec"
    spec_directory.mkdir()
    (spec_directory / "31-dangling.md").symlink_to("missing.md")
    (spec_directory / "32-directory.md").mkdir()
    monkeypatch.setattr(check_sdd_map, "SPEC_DIRECTORY", spec_directory)

    numbers, errors = check_sdd_map.read_spec_numbers()

    assert numbers == set()
    assert errors == [
        "numbered spec path is not a regular file: spec/31-dangling.md",
        "numbered spec path is not a regular file: spec/32-directory.md",
    ]


def test_indented_setext_example_is_not_an_index_heading(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
    § Index
    -------

## § Index

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_h1_ends_the_index_section(tmp_path, monkeypatch) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

 # Different section

- §0 foreign route
- Decision Log — foreign route
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == []
    assert errors == ["§ Index contains no bullets"]


def test_indented_duplicate_index_heading_is_rejected(tmp_path, monkeypatch) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

- §0 real route
- Decision Log — real route

 ## § Index
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == []
    assert errors == [
        "SDD.md must contain exactly one canonical '## § Index' heading "
        "and no variant spellings, found 2 candidate(s)"
    ]


def test_setext_heading_ends_the_index_section(tmp_path, monkeypatch) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

Different section
=================

- §0 foreign route
- Decision Log — foreign route
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == []
    assert errors == ["§ Index contains no bullets"]


def test_indented_code_list_marker_before_routes_is_ignored(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

    - §999 example shown as code

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_final_list_item_before_separator_is_not_a_setext_heading(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

- §0 real route
- Decision Log — real route
---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_indented_code_comment_marker_does_not_hide_foreign_heading() -> None:
    body = """\
## §30. Real section
    <!--
## §31. Foreign section
    -->
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_atx_text_without_marker_whitespace_is_not_an_index_heading(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
#§ Index

## § Index

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_section_heading_rejects_foreign_setext_root() -> None:
    body = """\
## §30. Real section
§31. Foreign section
--------------------
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_nested_list_heading_cannot_serve_as_section_root() -> None:
    body = """\
- item
  ## §30. Nested heading
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_index_text_with_unspaced_hashes_is_not_a_duplicate_heading(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index###

## § Index

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_unicode_whitespace_does_not_make_prose_a_list_item(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

1.\u00a0ordinary prose
-\u00a0ordinary prose

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_mixed_setext_markers_do_not_end_the_index_section(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

ordinary prose
=-=

- §0 real route
- Decision Log — real route

---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_index_separator_allows_markdown_indentation_and_trailing_space(
    tmp_path, monkeypatch
) -> None:
    sdd_path = tmp_path / "SDD.md"
    sdd_path.write_text(
        """\
## § Index

- §0 real route
- Decision Log — real route
"""
        + "   ---   \n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_sdd_map, "SDD_PATH", sdd_path)

    bullets, errors = check_sdd_map.read_index_bullets()

    assert bullets == ["- §0 real route", "- Decision Log — real route"]
    assert errors == []


def test_section_heading_rejects_zero_padded_foreign_atx_root() -> None:
    body = """\
## §30. Real section
## §031. Foreign section
"""

    assert not check_sdd_map.has_section_heading(body, 30)


def test_section_heading_rejects_zero_padded_foreign_setext_root() -> None:
    body = """\
## §30. Real section
§031. Foreign section
---------------------
"""

    assert not check_sdd_map.has_section_heading(body, 30)
