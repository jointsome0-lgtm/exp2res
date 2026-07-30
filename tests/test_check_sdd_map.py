from scripts.check_sdd_map import has_section_heading


def test_section_heading_ignores_shorter_fence_example_inside_longer_fence() -> None:
    body = """\
## §99. Wrong section
````markdown
```
## §30. Example inside code
```
````
"""

    assert not has_section_heading(body, 30)


def test_section_heading_ignores_mismatched_closing_fence() -> None:
    body = """\
## §99. Wrong section
~~~~
```
## §30. Example inside code
~~~~
"""

    assert not has_section_heading(body, 30)


def test_fence_with_info_string_does_not_close_open_fence() -> None:
    body = """\
## §99. Wrong section
```
```python
## §30. Example inside code
```
"""

    assert not has_section_heading(body, 30)


def test_section_heading_is_found_after_compatible_closing_fence() -> None:
    body = """\
```python
## §30. Example inside code
````
## §30. Real section
"""

    assert has_section_heading(body, 30)


def test_backtick_in_info_string_does_not_open_fence() -> None:
    body = """\
```not`a-fence
## §30. Real section
```
"""

    assert has_section_heading(body, 30)
