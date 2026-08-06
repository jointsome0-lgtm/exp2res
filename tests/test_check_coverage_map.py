from scripts.check_coverage_map import junit_identity


def test_junit_identity_keeps_colons_inside_a_parameter_id() -> None:
    node = "tests/test_cli_view_serve.py::test_urls[::1]"

    assert junit_identity(node) == (
        "tests.test_cli_view_serve",
        "test_urls[::1]",
    )


def test_junit_identity_still_includes_a_test_class() -> None:
    node = "tests/test_example.py::TestExample::test_case[value]"

    assert junit_identity(node) == (
        "tests.test_example.TestExample",
        "test_case[value]",
    )
