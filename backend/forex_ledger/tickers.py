import re

TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


def normalize_ticker(value: str | None) -> str | None:
    """Upper-cased, trimmed ticker (e.g. "brk.b" → "BRK.B"); rejects anything else."""
    if value is None:
        return None
    ticker = value.strip().upper()
    if not TICKER.match(ticker):
        raise ValueError(f"'{value}' isn't a valid ticker (letters, digits, '.' or '-')")
    return ticker
