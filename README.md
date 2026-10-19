# Forex Ledger

Find out what converting rupees to dollars (and back) really costs you when investing in US
stocks from India.

Record your INR↔USD conversions and stock trades (INDmoney by default, other platforms
supported). The app compares every conversion with the mid-market rate and with bank rates
(SBI, ICICI, HDFC, or any bank you add), and shows:

- the hidden forex markup, GST and brokerage you paid
- how much of your profit came from the stock vs. the rupee's movement
- a sell calculator: your net ₹ profit if you sell N shares at today's price and rate
- your break-even price and a TCS tracker for the ₹10 lakh LRS threshold

Everything runs on your own laptop.

> **Status:** the conversions ledger and bank-rate comparison work today. Stock trades and the
> sell calculator are next. See [SPEC.md](SPEC.md) for the full design.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. `git clone https://github.com/Saimanish123/forex_calculator.git && cd forex_calculator`
3. `uv run forex` — opens the app in your browser

Your data is saved in `data/forex.db` inside the project folder, with an automatic backup in
`data/backups/` every time the app starts. The app only downloads public exchange rates; your
entries never leave your computer.

## Data sources

- Minute-level mid-market rates: Yahoo Finance chart data (unofficial)
- Fallback mid-market rates: [Frankfurter](https://frankfurter.dev) (ECB) and [ExchangeRate-API](https://www.exchangerate-api.com) open access
- SBI TT rates: [sbi-fx-ratekeeper](https://github.com/sahilgupta/sbi-fx-ratekeeper)
- ICICI and HDFC card rates: each bank's public forex rate sheet

## Disclaimer

Not financial or tax advice. Rates, fees and tax figures are estimates. Always check your
platform statements and confirm tax with a CA.

## Credits

Bundled: [Preact](https://preactjs.com) and [htm](https://github.com/developit/htm) (MIT), the
[Inter](https://rsms.me/inter/) font (SIL OFL), icons adapted from [Lucide](https://lucide.dev) (ISC).

## License

[MIT](LICENSE)
