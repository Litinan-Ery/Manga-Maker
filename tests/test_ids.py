from backend.app.ids import uuid7


def test_uuid7_has_expected_version_and_variant() -> None:
    identifier = uuid7()
    assert identifier.version == 7
    assert identifier.variant == "specified in RFC 4122"


def test_uuid7_is_time_ordered_for_sequential_calls() -> None:
    identifiers = [uuid7() for _ in range(100)]
    timestamps = [identifier.int >> 80 for identifier in identifiers]
    assert timestamps == sorted(timestamps)
