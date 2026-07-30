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
