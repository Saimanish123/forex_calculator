# Forex Ledger

Find out what converting rupees to dollars (and back) really costs you when investing in US
stocks from India.

Record your INR↔USD conversions and stock trades (INDmoney by default, other platforms
supported). The app compares every conversion with the mid-market rate and with bank rates
(SBI, ICICI, HDFC, or any bank you add). Everything runs on your own laptop.

- **Dashboard** — net invested, realized profit split into stock, currency and costs, hidden costs
  paid, "if you sold everything today" with break-even, and a TCS (LRS) tracker
- **Conversions** — deposits, withdrawals and dividends, each compared with the market rate at that
  minute and with your bank's published rate
- **Stocks** — buys, sells, splits and corporate actions; holdings with FIFO lots and rupee cost
- **Sell calculator** — net rupee profit after brokerage, forex markups, GST and currency movement,
  break-even price, and a capital-gains tax estimate
- **Rates** — today's SBI, ICICI and HDFC rates against the mid-market rate

See [SPEC.md](SPEC.md) for the full design and every formula.

## Setup

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)
2. `git clone https://github.com/Saimanish123/forex_calculator.git && cd forex_calculator`
3. `uv run forex` — opens the app in your browser

Or double-click `Forex Ledger.command` (macOS) / `Forex Ledger.bat` (Windows) in the project
folder. To try it before entering real numbers, use **Load sample data** on the dashboard.

Your data is saved in `data/forex.db` inside the project folder, with an automatic backup in
`data/backups/` every time the app starts. The app only downloads public exchange rates; your
entries never leave your computer.

## Data sources

- Minute-level mid-market rates: Yahoo Finance chart data (unofficial)
- Fallback mid-market rates: [Frankfurter](https://frankfurter.dev) (ECB) and [ExchangeRate-API](https://www.exchangerate-api.com) open access
- SBI TT rates: [sbi-fx-ratekeeper](https://github.com/sahilgupta/sbi-fx-ratekeeper)
- ICICI and HDFC card rates: each bank's public forex rate sheet
- Share prices (optional): Yahoo Finance chart data

## Disclaimer

Not financial or tax advice. Rates, fees and tax figures are estimates. Always check your
platform statements and confirm tax with a CA.

## Credits

Bundled: [Preact](https://preactjs.com) and [htm](https://github.com/developit/htm) (MIT), the
[Inter](https://rsms.me/inter/) font (SIL OFL), icons adapted from [Lucide](https://lucide.dev) (ISC).

## License

[MIT](LICENSE)
