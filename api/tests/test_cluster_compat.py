import pytest

from megooflags_api.services.cluster.compat import incompatibility, minor_of


@pytest.mark.parametrize(
    ("worker", "control_plane", "ok"),
    [
        ("0.3.1", "0.3.9", True),
        ("0.3.0", "0.4.0", False),
        ("0.4.0", "0.3.0", False),
        ("1.2.0", "1.2.5", True),
        ("1.1.9", "1.2.0", True),
        ("1.0.0", "1.2.0", False),
        ("1.3.0", "1.2.0", False),
        ("2.0.0", "1.9.0", False),
        ("1.9.0", "2.0.0", False),
        ("0.3.0-rc.1", "0.3.0", True),
        ("unknown", "0.1.0", False),
        ("0.1", "0.1.0", False),
    ],
)
def test_incompatibility_table(worker: str, control_plane: str, ok: bool) -> None:
    assert (incompatibility(worker, control_plane) is None) is ok


def test_message_names_both_versions() -> None:
    message = incompatibility("0.3.0", "0.4.1")
    assert message is not None
    assert message.startswith("worker 0.3.0 cannot join control plane 0.4.1: ")
    assert message.endswith("; upgrade the control plane first, then the workers")


def test_minor_of() -> None:
    assert minor_of("1.4.2") == (1, 4)
    assert minor_of("1.4.2-rc.3") == (1, 4)
    with pytest.raises(ValueError):
        minor_of("x")
