from __future__ import annotations

import pytest

from app.services.plm.regulatory import cas


@pytest.mark.parametrize("value", ["64-17-5", "7732-18-5", "50-00-0", "7778-50-9", "9004-34-6"])
def test_valid_cas(value):
    assert cas.is_valid(value)
    assert cas.normalize(value) == value


@pytest.mark.parametrize("value", ["64-17-6", "7732-18-4", "1-00-0", "abc", "", None, "64175"])
def test_invalid_cas(value):
    assert not cas.is_valid(value)


def test_normalize_strips_whitespace_and_leading_zeros():
    assert cas.normalize(" 0064-17-5 ") == "64-17-5"
    assert cas.normalize("64－17－5") == "64-17-5"


@pytest.mark.parametrize(
    ("raw", "expected", "how"),
    [
        ("2079-10-07 00:00:00", "79-10-7", "excel_date"),  # acrylic acid, first block read as 20xx
        ("2068-12-02", "68-12-2", "excel_date"),  # DMF
        ("7446-09-05", "7446-09-5", "excel_date"),  # SO2: only the last block got zero-padded
        ("72321", "98-01-1", "excel_serial"),  # furfural: serial of 2098-01-01
        ("1883929", "7058-01-7", "excel_serial"),
        ("64-17-5", "64-17-5", "ok"),
    ],
)
def test_repair_excel_corruption(raw, expected, how):
    assert cas.repair(raw) == (expected, how)


def test_repair_never_guesses():
    # A date whose candidate readings all fail the checksum stays invalid.
    assert cas.repair("2079-10-08") == (None, "invalid")
    assert cas.repair("9999999") == (None, "invalid")
    assert cas.repair("") == (None, "empty")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1330-20-7;95-\n47-6;108-38-3", ["1330-20-7", "95-47-6", "108-38-3"]),
        ("25154-54-5;52\n8-29-0;99-65-\n0; 100-25-4", ["25154-54-5", "528-29-0", "99-65-0", "100-25-4"]),
        ("90-04-0；\n104-94-9", ["90-04-0", "104-94-9"]),
        ("64-17-5\n67-56-1", ["64-17-5", "67-56-1"]),  # newline as a separator
        ("", []),
    ],
)
def test_split_many(raw, expected):
    assert cas.split_many(raw) == expected
