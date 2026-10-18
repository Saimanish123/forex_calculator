# Forex Ledger

Find out what converting rupees to dollars (and back) really costs you when investing in US
stocks from India.

Record your INR↔USD conversions and stock trades (INDmoney by default, other platforms
supported). The app compares every conversion with the mid-market rate and with bank rates
(SBI, ICICI), and shows:

- the hidden forex markup, GST and brokerage you paid
- how much of your profit came from the stock vs. the rupee's movement
- a sell calculator: your net ₹ profit if you sell N shares at today's price and rate
- your break-even price and a TCS tracker for the ₹10 lakh LRS threshold

Everything runs on your own laptop. Your data stays in a local file and is never uploaded.

> **Status:** in development. See [SPEC.md](SPEC.md) for the full design.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. `git clone <this repo> && cd forex_calculator`
3. `uv run forex` (coming with phase 1)

## Data sources

- Minute-level mid-market rates: Yahoo Finance chart data (unofficial)
- Fallback mid-market rates: [Frankfurter](https://frankfurter.dev) (ECB) and [ExchangeRate-API](https://www.exchangerate-api.com) open access
- SBI TT rates: [sbi-fx-ratekeeper](https://github.com/sahilgupta/sbi-fx-ratekeeper)
- ICICI card rates: ICICI Bank's public forex rate page

## Disclaimer

Not financial or tax advice. Rates, fees and tax figures are estimates. Always check your
platform statements and confirm tax with a CA.

## License

[MIT](LICENSE)
