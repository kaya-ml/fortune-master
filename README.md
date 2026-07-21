# Integrated Divination Master (統合命占マスター版)

An integrated Japanese divination web app built with **Streamlit**. It combines five
traditional systems into a single reading and visualizes long-term and monthly
"fortune biorhythm" trends, with printable PDF reports.

## Features

- **Five divination systems in one reading**
  - Numerology (数秘術)
  - Western astrology (西洋占星術)
  - Four Pillars of Destiny / Bazi (四柱推命)
  - Nine Star Ki (九星気学)
  - Sukuyo astrology (宿曜占星術)
- **Fortune biorhythm charts** — an integrated yearly score (with adjustable
  weighting per system) plus a month-by-month view for the current year.
- **Display modes** — single person, two people side by side, or up to five
  people (each shown in a collapsible panel).
- **Client registration** — save a consultant's clients (with optional profile
  fields); saved details are reflected in the PDF report.
- **PDF reports** — generate A4 (detailed) or A5 (compact card) reading sheets.

## Requirements

Python 3.10+ and the packages listed in [`requirements.txt`](requirements.txt):
`streamlit`, `sxtwl`, `lunar-python`, `lunardate`, `ephem`, `reportlab`,
`plotly`, `kaleido`.

The `data/` folder must contain the reference files:
`shukuyo.json`, `kyusei.json`, `general.json`.

## Run locally

```bash
pip install -r requirements.txt
streamlit run fortune_master.py
```

The app opens in your browser at `http://localhost:8501`.

## Deployment (private / password-protected)

The app can be hosted on Streamlit Community Cloud. To restrict access, set a
password in the app's **Secrets**:

```toml
app_password = "your-password-here"
```

When `app_password` is set, visitors must enter it before using the app.
If it is not set (e.g. local development), no password is required.

> **Note on data persistence:** on hosts with an ephemeral filesystem
> (such as Streamlit Community Cloud), the local `clients.json` store may be
> reset on restart or redeploy. For durable client storage, use a host with a
> persistent disk or an external store (e.g. Google Sheets or a database).

## Notes

- Static graph images in the PDF are rendered with **Kaleido** (which relies on
  Chromium). On-screen interactive charts do not require it, and PDFs are still
  generated without the embedded graph image if Kaleido is unavailable.
- Month-level scores use approximate solar-term / new-moon boundaries.

## Disclaimer

This application is intended for entertainment and reference purposes only.
It does not provide professional, medical, legal, or financial advice. If it is
used to handle personal information of third parties, ensure appropriate consent
and data-protection measures are in place.
