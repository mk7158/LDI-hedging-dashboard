# LDI Hedging Dashboard

An interactive Liability-Driven Investment (LDI) hedging tool for US pension funds, built with Python and Streamlit.

## What it does

- Models a 30-year USD pension liability ($15M × 1.025^t, discounted at 4.45%)
- Calculates PV01 sensitivity for US Treasuries (2Y/5Y/10Y/30Y) and USD Swaps (10Y/30Y)
- Optimizes a hedge portfolio using per-bucket KRD matching with ±5% tolerance
- Stress-tests the portfolio against yield curve shocks (±100 bps)
- Outputs an optimal trade execution list

## How to run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Project structure

```
ldi_python/
├── ldi_engine.py   # Core LDI math: DCF, PV01, KRD optimizer
├── app.py          # Streamlit dashboard UI
├── requirements.txt
└── README.md
```

## Methodology

Liabilities are valued as a discounted cash flow stream. PV01 is computed via a 1bp parallel shift on the discount curve. The optimizer matches each KRD maturity bucket (Short 0-5Y / Intermediate 5-15Y / Long 15-30Y+) independently within ±5% tolerance, allocating the highest PV01-per-million instrument first to minimise capital deployed. No commercial solvers (Gurobi, QuantLib) are required.
