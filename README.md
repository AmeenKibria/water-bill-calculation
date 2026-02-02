# Water Bill Split

Small Streamlit app to split a shared water bill:
- Basic fees are split equally.
- Usage fees are split based on sub-meter consumption.
- Main vs sub-meter mismatch is displayed by default (display-only).
- Optional override lets you allocate mismatch 50/50 or proportionally.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
streamlit run app.py
```

## Pages

**Page 1: Split current bill**
- Enter the billing period, basic fees, usage fees, and sub-meter usage.
- Optional: add main meter readings to display mismatch.
- Save the period to Google Sheets history for later true-ups.

**Page 2: True-up / Reconciliation**
- Enter a correction amount (positive or negative).
- Select saved periods or enter usage manually.
- Splits the true-up by usage ratio and shows who owes whom.

**Page 3: History**
- Shows saved periods, computed shares, and mismatch policy.

## Language

Use the sidebar selector to switch between English and Finnish.

## Mismatch thresholds

The app always displays mismatch (if main meter is provided) and classifies it:
- OK: `abs(mismatch_m3) ≤ 1.0` or `abs(mismatch_pct) ≤ 5%`
- Warning: `1–3 m³` or `5–10%`
- Investigate: `> 3 m³` or `> 10%`

## Mismatch override (manual)

Default is display-only. You can manually override to allocate mismatch:
- **Ignore mismatch (default)**: split by sub-meter usage only.
- **Split 50/50**: add half of the mismatch to each sub-meter usage.
- **Split proportional**: allocate mismatch by sub-meter usage ratio.

## Example values (from your bill)

- Basic fees total: `84.03`
- Usage fees total: `222.13`

For readings, enter previous and current values for each meter so the app can
compute usage deltas.

## How to use when HSY bill arrives

1. Open the HSY bill and note:
   - Billing period start/end
   - Basic fees total (perusmaksu)
   - Consumption total (käyttömaksut)
2. Read both sub-meters (AS-1/Ameen and AS-2/Jussi) for the same period:
   - If you have start/end readings, choose **Readings (start/end)**.
   - If you only know usage, choose **Usage only**.
3. (Optional) Enter the main meter readings to see the mismatch status.
4. Click **Calculate split** and review totals.
5. Use the **Settlement** line to pay Jussi (AS-2) and copy the WhatsApp line.
6. Keep **Save this period to history** checked so true-ups can be calculated later.

## When a true-up (credit/extra charge) arrives

1. Go to **True-up / Reconciliation**.
2. Enter the correction amount (positive or negative) and the correction period.
3. Select the stored periods that the correction covers (or enter manual usage).
4. Click **Calculate true-up** and settle using the shown shares.

## Google Sheets storage

This app stores periods and true-ups in two tabs:
- `periods`
- `trueups`

The sheet ID is configured via Streamlit secrets as `SHEET_ID`.

## Streamlit Community Cloud deployment

1. Push this repo to GitHub.
2. Go to Streamlit Cloud and deploy the app from `app.py`.
3. Add secrets in Streamlit Cloud:

```
SHEET_ID = "your_google_sheet_id"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

4. Share the Google Sheet with the `client_email` from the service account.
5. Redeploy and test.

## CI behavior

- GitHub Actions runs on:
  - PRs targeting `master`
  - Pushes to `master`
- Deploy to Streamlit should happen only after CI passes.
