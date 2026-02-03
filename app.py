import csv
import hashlib
import io
import json
import os
from datetime import datetime, time
from pathlib import Path

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials
from streamlit.runtime.secrets import StreamlitSecretNotFoundError


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────
SESSION_SECRET = "water_bill_hirvensarvi_16b"  # Used for session token generation


def hash_password(password: str) -> str:
    """Hash password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def generate_session_token(username: str) -> str:
    """Generate a session token for persistent login."""
    return hashlib.sha256(f"{username}:{SESSION_SECRET}".encode()).hexdigest()[:16]


def validate_session_token(username: str, token: str) -> bool:
    """Validate a session token."""
    expected = generate_session_token(username)
    return token == expected


def check_credentials(username: str, password: str) -> bool:
    """Check if username/password match stored credentials."""
    try:
        passwords = st.secrets.get("passwords", {})
        if username.lower() in passwords:
            stored_hash = passwords[username.lower()]
            return hash_password(password) == stored_hash
    except StreamlitSecretNotFoundError:
        pass
    return False


def restore_session():
    """Try to restore session from query params."""
    params = st.query_params
    if "user" in params and "token" in params:
        username = params["user"]
        token = params["token"]
        if validate_session_token(username, token):
            st.session_state.authenticated = True
            st.session_state.username = username
            return True
    return False


def save_session(username: str):
    """Save session to query params for persistence across reloads."""
    token = generate_session_token(username)
    st.query_params["user"] = username
    st.query_params["token"] = token


def clear_session():
    """Clear session from query params."""
    if "user" in st.query_params:
        del st.query_params["user"]
    if "token" in st.query_params:
        del st.query_params["token"]


def login_form():
    """Display login form and handle authentication."""
    st.title("Water Bill Split")
    st.subheader("Login")
    
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        
        if submitted:
            if check_credentials(username, password):
                st.session_state.authenticated = True
                st.session_state.username = username.lower()
                save_session(username.lower())
                st.rerun()
            else:
                st.error("Invalid username or password")
    
    return False


def logout():
    """Log out the current user."""
    st.session_state.authenticated = False
    st.session_state.username = None
    clear_session()
    st.rerun()

from sheets_storage import (
    PERIODS_HEADERS,
    TRUEUPS_HEADERS,
    normalize_period_record,
    normalize_trueup_record,
)
from utils import (
    build_simple_pdf,
    compute_split,
    compute_trueup,
    format_date,
    format_eur,
    format_m3,
    format_number,
    mismatch_status,
    parse_number,
    validate_decimal_places,
    wrap_lines,
)

DATA_DIR = Path(__file__).parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"



def load_local_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def load_service_account_info():
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
        if "service_account" in st.secrets:
            return json.loads(st.secrets["service_account"])
    except StreamlitSecretNotFoundError:
        pass
    file_path = os.environ.get("SERVICE_ACCOUNT_FILE")
    if file_path and Path(file_path).exists():
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    local_file = Path(__file__).parent / "durable-limiter-456709-k3-d70697c888b2.json"
    if local_file.exists():
        return json.loads(local_file.read_text(encoding="utf-8"))
    return None


@st.cache_resource(ttl=600, show_spinner=False)  # Cache connection for 10 minutes
def get_sheet():
    try:
        sheet_id = st.secrets.get("SHEET_ID")
    except StreamlitSecretNotFoundError:
        sheet_id = None
    service_info = load_service_account_info()
    if not sheet_id or not service_info:
        return None
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(service_info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


@st.cache_data(ttl=300, show_spinner=False)  # Cache for 5 minutes
def get_records(tab_name: str) -> list[dict]:
    sheet = get_sheet()
    if sheet is None:
        return []
    try:
        worksheet = sheet.worksheet(tab_name)
        # Get all values and build records manually to handle empty/duplicate headers
        all_values = worksheet.get_all_values()
        if not all_values:
            return []
        
        # Get headers from first row, filter out empty ones
        headers = all_values[0]
        # Find indices of non-empty, unique headers
        seen = set()
        valid_indices = []
        clean_headers = []
        for i, h in enumerate(headers):
            if h and h not in seen:
                seen.add(h)
                valid_indices.append(i)
                clean_headers.append(h)
        
        # Build records from remaining rows
        records = []
        for row in all_values[1:]:
            record = {}
            for idx, header in zip(valid_indices, clean_headers):
                record[header] = row[idx] if idx < len(row) else ""
            records.append(record)
        return records
    except Exception as e:
        st.warning(tr(f"Error reading {tab_name}: {e}", f"Virhe luettaessa {tab_name}: {e}"))
        return []


def clear_records_cache():
    """Clear the cached records to force fresh data fetch."""
    # Only works when caching is enabled
    if hasattr(get_records, 'clear'):
        get_records.clear()


def append_record(tab_name: str, headers: list[str], record: dict) -> bool:
    """Append a record to Google Sheets. Returns True if successful."""
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        worksheet = sheet.worksheet(tab_name)
        row = [record.get(header, "") for header in headers]
        worksheet.append_row(row, value_input_option="RAW")
        # Clear cache so fresh data is shown
        clear_records_cache()
        return True
    except Exception as e:
        st.error(tr(f"Error saving to Google Sheets: {e}", f"Virhe tallennettaessa Google Sheetsiin: {e}"))
        return False


def local_periods_records() -> list[dict]:
    records = []
    for entry in load_local_history():
        records.append(
            {
                "Period start": format_date(entry.get("period_start")),
                "Period end": format_date(entry.get("period_end")),
                "Invoice number": entry.get("invoice_number"),
                "Estimated water": entry.get("estimated_water"),
                "Due date": format_date(entry.get("due_date")),
                "Reading start": entry.get("reading_start"),
                "Reading end": entry.get("reading_end"),
                "Main usage": entry.get("main_use"),
                "AS-1 usage": entry.get("s1_use"),
                "AS-2 usage": entry.get("s2_use"),
                "Basic fees": entry.get("basic_fees"),
                "Usage fees": entry.get("usage_fees"),
                "AS-1 total": entry.get("total_1"),
                "AS-2 total": entry.get("total_2"),
                "Mismatch (m3)": entry.get("mismatch_m3"),
                "Mismatch (%)": entry.get("mismatch_pct"),
                "Saved at": format_date(entry.get("created_at")),
            }
        )
    return records


st.set_page_config(page_title="Water Bill Split", layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1100px;
    }
    [data-testid="stSidebar"] .sidebar-spacer {
        height: 24px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Authentication check
# ─────────────────────────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = None

# Try to restore session from URL params (persists across page reloads)
if not st.session_state.authenticated:
    restore_session()

if not st.session_state.authenticated:
    login_form()
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Main app (only shown when authenticated)
# ─────────────────────────────────────────────────────────────────────────────

if "language" not in st.session_state:
    st.session_state.language = "English"

language = st.sidebar.selectbox("Language / Kieli", ["English", "Suomi"], key="language")
is_fi = language == "Suomi"


def tr(en: str, fi: str) -> str:
    return fi if is_fi else en


# Error message translations for exceptions from utils.py
ERROR_TRANSLATIONS = {
    "Sub-meter usage cannot be negative.": "Alamittarin kulutus ei voi olla negatiivinen.",
    "Total sub-meter usage must be greater than 0.": "Alamittarien kokonaiskulutuksen on oltava suurempi kuin 0.",
    "Main meter usage must be greater than 0.": "Päämittarin kulutuksen on oltava suurempi kuin 0.",
    "Adjusted usage became negative.": "Oikaistu kulutus muuttui negatiiviseksi.",
    "Total usage must be greater than 0.": "Kokonaiskulutuksen on oltava suurempi kuin 0.",
}


def tr_error(msg: str) -> str:
    """Translate error messages from utils.py"""
    if is_fi and msg in ERROR_TRANSLATIONS:
        return ERROR_TRANSLATIONS[msg]
    return msg


page_definitions = [
    {"id": "split", "en": "Split current bill", "fi": "Jaa nykyinen lasku"},
    {"id": "trueup", "en": "True-up / Reconciliation", "fi": "Oikaisu / Reconciliation"},
    {"id": "history", "en": "History", "fi": "Historia"},
]
page_ids = [page_def["id"] for page_def in page_definitions]
label_to_id = {
    page_def["en"]: page_def["id"] for page_def in page_definitions
} | {page_def["fi"]: page_def["id"] for page_def in page_definitions}
current_page_id = st.session_state.get("page_id", "split")
if current_page_id not in page_ids:
    mapped_id = label_to_id.get(current_page_id)
    if mapped_id:
        st.session_state.page_id = mapped_id
        current_page_id = mapped_id
page_index = page_ids.index(current_page_id) if current_page_id in page_ids else 0

st.sidebar.markdown('<div class="sidebar-spacer"></div>', unsafe_allow_html=True)
page_id = st.sidebar.selectbox(
    tr("Page", "Sivu"),
    page_ids,
    index=page_index,
    format_func=lambda page_value: next(
        page_def["fi"] if is_fi else page_def["en"]
        for page_def in page_definitions
        if page_def["id"] == page_value
    ),
    key="page_id",
)

# User info and logout
st.sidebar.markdown("---")
st.sidebar.markdown(f"**{tr('Logged in as', 'Kirjautunut')}:** {st.session_state.username.capitalize()}")
if st.sidebar.button(tr("Logout", "Kirjaudu ulos")):
    logout()

st.title(tr("Water Bill Split", "Vesilaskun jako"))


if page_id == "split":
    st.header(
        tr(
            "Split the current HSY bill for Hirvensarvi 16 B",
            "Jaa nykyinen HSY-lasku (Hirvensarvi 16 B)",
        )
    )
    st.caption(
        tr(
            "Basic fees are split 50/50. Consumption is split by sub-meter usage.",
            "Perusmaksu jaetaan 50/50. Käyttömaksu jaetaan kulutuksen mukaan.",
        )
    )
    with st.expander(tr("How this works", "Miten tämä toimii")):
        st.markdown(
            tr(
                "- Basic fees are split 50/50.\n"
                "- Usage fees are split by sub-meter usage ratio.\n"
                "- Mismatch is shown for awareness; default is display-only.\n"
                "- You can manually override mismatch allocation if needed.",
                "- Perusmaksu jaetaan 50/50.\n"
                "- Käyttömaksu jaetaan alamittarien kulutussuhteessa.\n"
                "- Poikkeama näytetään; oletus on vain näyttö.\n"
                "- Poikkeaman jaon voi tarvittaessa yliajaa.",
            )
        )

    st.subheader(tr("Billing period", "Laskutusjakso"))
    col_period = st.columns(2)
    with col_period[0]:
        period_start = st.date_input(
            tr("Start date", "Alkupäivä"),
            format="DD/MM/YYYY",
        )
    with col_period[1]:
        period_end = st.date_input(
            tr("End date", "Loppupäivä"),
            format="DD/MM/YYYY",
        )
    
    col_invoice = st.columns(3)
    with col_invoice[0]:
        invoice_number = st.text_input(
            tr("Invoice number", "Laskun numero"),
            placeholder="12345",
            help=tr("Invoice reference number", "Laskun viitenumero"),
        )
    with col_invoice[1]:
        estimated_water_text = st.text_input(
            tr("Estimated water (m³)", "Arvioitu vesi (m³)"),
            placeholder="0",
            help=tr("Estimated water amount on invoice", "Laskun arvioitu vesimäärä"),
        )
        estimated_water = parse_number(estimated_water_text)
    with col_invoice[2]:
        due_date = st.date_input(
            tr("Due date", "Eräpäivä"),
            format="DD/MM/YYYY",
            help=tr("Payment due date", "Maksun eräpäivä"),
        )

    st.subheader(tr("Totals bill", "Laskun summat"))
    bill_cols = st.columns([1, 1, 1])
    with bill_cols[0]:
        basic_fees_text = st.text_input(
            tr("Basic fees total", "Perusmaksut yhteensä"),
            placeholder="0,00€",
        )
    with bill_cols[1]:
        usage_fees_text = st.text_input(
            tr("Consumption total", "Käyttömaksut yhteensä"),
            placeholder="0,00€",
        )
    basic_fees = parse_number(basic_fees_text)
    usage_fees = parse_number(usage_fees_text)
    with bill_cols[2]:
        if basic_fees is not None and usage_fees is not None:
            total_bill = basic_fees + usage_fees
            st.metric(tr("Total bill", "Lasku yhteensä"), format_eur(total_bill))
        else:
            st.metric(tr("Total bill", "Lasku yhteensä"), "-")

    st.subheader(tr("Meter readings (m³)", "Mittarilukemat (m³)"))
    st.caption(
        tr(
            "Enter main meter (optional) and sub-meter readings. Main meter is used for mismatch display.",
            "Syötä päämittari (valinnainen) ja alamittarilukemat. Päämittaria käytetään poikkeaman näyttöön.",
        )
    )
    usage_mode = st.radio(
        tr("Input method", "Syöttötapa"),
        [tr("Readings (start/end)", "Lukemat (alku/loppu)"), tr("Usage only", "Vain kulutus")],
        horizontal=True,
    )
    st.caption(
        tr(
            "Readings = start and end values. Usage only = total usage.",
            "Lukemat = alku ja loppu. Vain kulutus = kokonaiskulutus.",
        )
    )
    s1_start_text = None
    s1_end_text = None
    s1_use_text = None
    s2_start_text = None
    s2_end_text = None
    s2_use_text = None
    main_start_text = None
    main_end_text = None
    main_use_text = None

    # Row 1: Start readings
    row1_cols = st.columns([0.7, 0.5, 1.0, 1.0, 1.0])
    with row1_cols[0]:
        reading_start_date = st.date_input(
            tr("Reading start", "Lukema alku"),
            format="DD/MM/YYYY",
            key="reading_start_date",
        )
    with row1_cols[1]:
        reading_start_time = st.time_input(
            tr("Time", "Aika"),
            value=time(0, 0),
            key="reading_start_time",
        )
    with row1_cols[2]:
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            main_start_text = st.text_input(
                tr("Main start", "Päämittari alku"),
                placeholder="0",
                help=tr("Optional. Whole number.", "Valinnainen. Kokonaisluku."),
                key="main_start_text",
            )
        else:
            main_use_text = st.text_input(
                tr("Main usage (opt)", "Päämittari (val)"),
                placeholder="0",
                help=tr("Optional. Whole number.", "Valinnainen. Kokonaisluku."),
                key="main_use_text",
            )
    with row1_cols[3]:
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            s1_start_text = st.text_input(
                tr("AS-1 start (Ameen)", "AS-1 alku (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s1_start_text",
            )
            if s1_start_text and not validate_decimal_places(s1_start_text, 3):
                st.error(tr("AS-1 start: max 3 decimals.", "AS-1 alku: enintään 3 desimaalia."))
        else:
            s1_use_text = st.text_input(
                tr("AS-1 usage (Ameen)", "AS-1 kulutus (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s1_use_text",
            )
            if s1_use_text and not validate_decimal_places(s1_use_text, 3):
                st.error(tr("AS-1 usage: max 3 decimals.", "AS-1 kulutus: enintään 3 desimaalia."))
    with row1_cols[4]:
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            s2_start_text = st.text_input(
                tr("AS-2 start (Jussi)", "AS-2 alku (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s2_start_text",
            )
            if s2_start_text and not validate_decimal_places(s2_start_text, 3):
                st.error(tr("AS-2 start: max 3 decimals.", "AS-2 alku: enintään 3 desimaalia."))
        else:
            s2_use_text = st.text_input(
                tr("AS-2 usage (Jussi)", "AS-2 kulutus (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s2_use_text",
            )
            if s2_use_text and not validate_decimal_places(s2_use_text, 3):
                st.error(tr("AS-2 usage: max 3 decimals.", "AS-2 kulutus: enintään 3 desimaalia."))

    # Row 2: End readings (only in Readings mode)
    if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
        row2_cols = st.columns([0.7, 0.5, 1.0, 1.0, 1.0])
        with row2_cols[0]:
            reading_end_date = st.date_input(
                tr("Reading end", "Lukema loppu"),
                format="DD/MM/YYYY",
                key="reading_end_date",
            )
        with row2_cols[1]:
            reading_end_time = st.time_input(
                tr("Time", "Aika"),
                value=time(0, 0),
                key="reading_end_time",
            )
        with row2_cols[2]:
            main_end_text = st.text_input(
                tr("Main end", "Päämittari loppu"),
                placeholder="0",
                help=tr("Optional. Whole number.", "Valinnainen. Kokonaisluku."),
                key="main_end_text",
            )
        with row2_cols[3]:
            s1_end_text = st.text_input(
                tr("AS-1 end (Ameen)", "AS-1 loppu (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s1_end_text",
            )
            if s1_end_text and not validate_decimal_places(s1_end_text, 3):
                st.error(tr("AS-1 end: max 3 decimals.", "AS-1 loppu: enintään 3 desimaalia."))
        with row2_cols[4]:
            s2_end_text = st.text_input(
                tr("AS-2 end (Jussi)", "AS-2 loppu (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Käytä pilkkua. Enintään 3 desimaalia."),
                key="s2_end_text",
            )
            if s2_end_text and not validate_decimal_places(s2_end_text, 3):
                st.error(tr("AS-2 end: max 3 decimals.", "AS-2 loppu: enintään 3 desimaalia."))
        
        # Parse readings mode values
        s1_start = parse_number(s1_start_text)
        s1_end = parse_number(s1_end_text)
        s1_use = s1_end - s1_start if s1_start is not None and s1_end is not None else None
        s2_start = parse_number(s2_start_text)
        s2_end = parse_number(s2_end_text)
        s2_use = s2_end - s2_start if s2_start is not None and s2_end is not None else None
        main_start = parse_number(main_start_text)
        main_end = parse_number(main_end_text)
    else:
        # Usage only mode - set defaults for end readings
        reading_end_date = reading_start_date
        reading_end_time = reading_start_time
        s1_use = parse_number(s1_use_text)
        s2_use = parse_number(s2_use_text)
        s1_start = None
        s1_end = None
        s2_start = None
        s2_end = None
        main_start = None
        main_end = None
        main_use_val = parse_number(main_use_text)
    
    # Determine if main meter is provided
    use_main = False
    if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
        if main_start is not None and main_end is not None and main_end > main_start:
            use_main = True
    else:
        if main_use_text and parse_number(main_use_text) is not None:
            use_main = True
            main_start = 0
            main_end = parse_number(main_use_text)

    st.subheader(tr("Mismatch allocation (manual override)", "Poikkeaman jako (manuaalinen yliajo)"))
    policy_ignore = tr("Ignore mismatch (display-only)", "Ohita poikkeama (vain näyttö)")
    policy_half = tr("Split mismatch 50/50", "Jaa poikkeama 50/50")
    policy_prop = tr("Split mismatch proportional", "Jaa poikkeama suhteessa")
    mismatch_policy_label = st.selectbox(
        tr("Policy", "Käytäntö"),
        [
            policy_ignore,
            policy_half,
            policy_prop,
        ],
        index=0,
        help=(
            tr(
                "Optional: decide how to split the difference between main and sub-meters.\n"
                "Default is display-only. Overrides apply only if main meter "
                "readings are provided.",
                "Oletus on vain näyttö. Yliajo toimii vain, jos päämittarin "
                "Valinnainen: miten jaetaan päämittarin ja alamittarien erotus.\n"
                "lukemat on annettu.",
            )
        ),
    )

    save_entry = st.checkbox(tr("Save this period to history", "Tallenna jakso historiaan"), value=True)
    submitted = st.button(tr("Calculate split", "Laske jako"))

    if submitted:
        errors = []
        if s1_use is None:
            errors.append(tr("AS-1 usage is required.", "AS-1 kulutus vaaditaan."))
        if s2_use is None:
            errors.append(tr("AS-2 usage is required.", "AS-2 kulutus vaaditaan."))
        if basic_fees is None:
            errors.append(tr("Basic fees total is required.", "Perusmaksut yhteensä vaaditaan."))
        if usage_fees is None:
            errors.append(tr("Consumption total is required.", "Käyttömaksut yhteensä vaaditaan."))
        if not validate_decimal_places(basic_fees_text, 2):
            errors.append(tr("Basic fees must have at most 2 decimals.", "Perusmaksu enintään 2 desimaalia."))
        if not validate_decimal_places(usage_fees_text, 2):
            errors.append(tr("Consumption total must have at most 2 decimals.", "Käyttömaksu enintään 2 desimaalia."))
        if s1_start is not None and not validate_decimal_places(s1_start_text, 3):
            errors.append(tr("AS-1 start must have at most 3 decimals.", "AS-1 alku enintään 3 desimaalia."))
        if s1_end is not None and not validate_decimal_places(s1_end_text, 3):
            errors.append(tr("AS-1 end must have at most 3 decimals.", "AS-1 loppu enintään 3 desimaalia."))
        if s2_start is not None and not validate_decimal_places(s2_start_text, 3):
            errors.append(tr("AS-2 start must have at most 3 decimals.", "AS-2 alku enintään 3 desimaalia."))
        if s2_end is not None and not validate_decimal_places(s2_end_text, 3):
            errors.append(tr("AS-2 end must have at most 3 decimals.", "AS-2 loppu enintään 3 desimaalia."))
        if usage_mode == tr("Usage only", "Vain kulutus"):
            if s1_use_text is not None and not validate_decimal_places(s1_use_text, 3):
                errors.append(tr("AS-1 usage must have at most 3 decimals.", "AS-1 kulutus enintään 3 desimaalia."))
            if s2_use_text is not None and not validate_decimal_places(s2_use_text, 3):
                errors.append(tr("AS-2 usage must have at most 3 decimals.", "AS-2 kulutus enintään 3 desimaalia."))
        if s1_use is not None and s2_use is not None and (s1_use < 0 or s2_use < 0):
            errors.append(tr("Sub-meter usage cannot be negative.", "Alamittarin kulutus ei voi olla negatiivinen."))
        if use_main and main_start is not None and main_end is not None:
            if main_end <= main_start:
                errors.append(tr("Main meter usage must be greater than 0.", "Päämittarin kulutuksen on oltava suurempi kuin 0."))
        if s1_use is not None and s2_use is not None and s1_use + s2_use <= 0:
            errors.append(tr("Total sub-meter usage must be greater than 0.", "Alamittarien kokonaiskulutuksen on oltava suurempi kuin 0."))
        if (
            mismatch_policy_label != policy_ignore
            and not use_main
        ):
            errors.append(tr("Mismatch override requires main meter readings.", "Poikkeaman yliajo vaatii päämittarin lukemat."))

        if errors:
            for err in errors:
                st.error(err)
        else:
            sub_sum = s1_use + s2_use
            main_use = None
            if use_main and main_start is not None and main_end is not None:
                main_use = main_end - main_start

            if mismatch_policy_label == policy_half:
                mismatch_policy = "half"
            elif mismatch_policy_label == policy_prop:
                mismatch_policy = "proportional"
            else:
                mismatch_policy = "ignore"

            try:
                split = compute_split(
                    s1_use=s1_use,
                    s2_use=s2_use,
                    basic_fees=basic_fees,
                    usage_fees=usage_fees,
                    mismatch_policy=mismatch_policy,
                    main_use=main_use,
                )
            except ValueError as exc:
                st.error(tr_error(str(exc)))
                st.stop()

            adj_s1_use = split["adj_s1_use"]
            adj_s2_use = split["adj_s2_use"]
            usage_share_1 = split["usage_share_1"]
            usage_share_2 = split["usage_share_2"]
            basic_share = split["basic_share"]
            mismatch_m3 = split["mismatch_m3"]
            mismatch_pct = split["mismatch_pct"]

            st.success(tr("Split calculated.", "Jako laskettu."))
            st.markdown(f"### {tr('Summary', 'Yhteenveto')}")
            st.table(
                [
                    {
                        tr("Person", "Henkilö"): "AS-1 (Ameen)",
                        tr("Usage", "Kulutus"): format_m3(adj_s1_use),
                        tr("Usage fees", "Käyttömaksu"): format_eur(usage_share_1),
                        tr("Basic fees", "Perusmaksu"): format_eur(basic_share),
                        tr("Total", "Yhteensa"): format_eur(basic_share + usage_share_1),
                    },
                    {
                        tr("Person", "Henkilö"): "AS-2 (Jussi)",
                        tr("Usage", "Kulutus"): format_m3(adj_s2_use),
                        tr("Usage fees", "Käyttömaksu"): format_eur(usage_share_2),
                        tr("Basic fees", "Perusmaksu"): format_eur(basic_share),
                        tr("Total", "Yhteensa"): format_eur(basic_share + usage_share_2),
                    },
                ]
            )

            st.markdown(f"### {tr('Settlement', 'Selvitys')}")
            total_ameen = basic_share + usage_share_1
            total_jussi = basic_share + usage_share_2
            st.write(
                {
                    tr("Total for Ameen", "Ameen yhteensä"): format_eur(total_ameen),
                    tr("Total for Jussi", "Jussi yhteensä"): format_eur(total_jussi),
                    tr("Ameen will pay Jussi", "Ameen maksaa Jussille"): format_eur(total_ameen),
                }
            )
            whatsapp_line = (
                (
                    "This period: Jussi total "
                    f"{format_eur(total_jussi)}, Ameen total "
                    f"{format_eur(total_ameen)} → Ameen pays Jussi "
                    f"{format_eur(total_ameen)}."
                )
                if not is_fi
                else (
                    "Tällä jaksolla: Jussi yhteensä "
                    f"{format_eur(total_jussi)}, Ameen yhteensä "
                    f"{format_eur(total_ameen)} → Ameen maksaa Jussille "
                    f"{format_eur(total_ameen)}."
                )
            )
            st.text_input(
                tr("WhatsApp copy/paste", "WhatsApp kopioi/liita"),
                value=whatsapp_line,
            )

            st.markdown(f"### {tr('Mismatch (always shown)', 'Poikkeama (aina näytetään)')}")
            if use_main and main_use is not None:
                status_code = mismatch_status(mismatch_m3, mismatch_pct)
                status_label = {
                    "ok": tr("OK", "OK"),
                    "warning": tr("Warning", "Varoitus"),
                    "investigate": tr("Investigate", "Tarkista"),
                }[status_code]
                message = {
                    "ok": tr("OK (likely rounding/timing)", "OK (todennakoisesti pyoristys/ajoitus)"),
                    "warning": tr("Warning: check readings", "Varoitus: tarkista lukemat"),
                    "investigate": tr("Investigate: mismatch is large", "Tarkista: poikkeama on suuri"),
                }[status_code]
                st.write(
                    {
                        tr("Main usage", "Päämittarin kulutus"): format_m3(main_use),
                        tr("Sub-meter total", "Alamittarit yhteensä"): format_m3(sub_sum),
                        tr("Mismatch (m³)", "Poikkeama (m³)"): format_m3(mismatch_m3),
                        tr("Mismatch (%)", "Poikkeama (%)"): (
                            f"{format_number(mismatch_pct * 100, 2)}%"
                            if mismatch_pct is not None
                            else tr("N/A", "Ei saatavilla")
                        ),
                        tr("Status", "Tila"): status_label,
                    }
                )
                if status_code == "ok":
                    st.info(message)
                elif status_code == "warning":
                    st.warning(message)
                else:
                    st.error(message)
                st.write({tr("Mismatch policy", "Poikkeaman kaytanto"): mismatch_policy_label})
            else:
                st.info(tr("Mismatch not available (main meter not provided).", "Poikkeamaa ei voi laskea (päämittari puuttuu)."))

            reading_start = f"{reading_start_date.strftime('%d/%m/%Y')} {reading_start_time.strftime('%H:%M')}"
            reading_end = f"{reading_end_date.strftime('%d/%m/%Y')} {reading_end_time.strftime('%H:%M')}"

            if save_entry:
                record = {
                    "Period start": period_start.strftime("%d/%m/%Y"),
                    "Period end": period_end.strftime("%d/%m/%Y"),
                    "Invoice number": invoice_number or "",
                    "Estimated water": estimated_water,
                    "Due date": due_date.strftime("%d/%m/%Y"),
                    "Reading start": reading_start,
                    "Reading end": reading_end,
                    "Main usage": main_use,
                    "AS-1 usage": s1_use,
                    "AS-2 usage": s2_use,
                    "Basic fees": basic_fees,
                    "Usage fees": usage_fees,
                    "AS-1 total": basic_share + usage_share_1,
                    "AS-2 total": basic_share + usage_share_2,
                    "Mismatch (m3)": mismatch_m3,
                    "Mismatch (%)": mismatch_pct,
                    "Saved at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                }
                if get_sheet():
                    if append_record("periods", PERIODS_HEADERS, record):
                        st.success(tr("Saved to history (Google Sheets).", "Tallennettu historiaan (Google Sheets)."))
                    # Error message already shown by append_record if failed
                else:
                    st.warning(tr("Google Sheets not configured. Saving locally.", "Google Sheets ei ole määritetty. Tallennetaan paikallisesti."))
                    history = load_local_history()
                    history.append(
                        {
                            "period_start": str(period_start),
                            "period_end": str(period_end),
                            "invoice_number": invoice_number or "",
                            "estimated_water": estimated_water,
                            "due_date": str(due_date),
                            "reading_start": reading_start,
                            "reading_end": reading_end,
                            "basic_fees": basic_fees,
                            "usage_fees": usage_fees,
                            "s1_start": s1_start,
                            "s1_end": s1_end,
                            "s2_start": s2_start,
                            "s2_end": s2_end,
                            "s1_use": s1_use,
                            "s2_use": s2_use,
                            "adj_s1_use": adj_s1_use,
                            "adj_s2_use": adj_s2_use,
                            "main_start": main_start,
                            "main_end": main_end,
                            "main_use": main_use,
                            "mismatch_m3": mismatch_m3,
                            "mismatch_pct": mismatch_pct,
                            "mismatch_policy": mismatch_policy,
                            "usage_share_1": usage_share_1,
                            "usage_share_2": usage_share_2,
                            "basic_share": basic_share,
                            "total_1": basic_share + usage_share_1,
                            "total_2": basic_share + usage_share_2,
                            "created_at": datetime.now().isoformat(),
                        }
                    )
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
                    st.warning(
                        tr(
                            "Sheets not configured; saved to local history.json.",
                            "Sheets ei ole konfiguroitu; tallennettiin paikalliseen history.json-tiedostoon.",
                        )
                    )

elif page_id == "trueup":
    st.header(tr("True-up / Reconciliation", "Oikaisu / Reconciliation"))
    st.caption(
        tr(
            "Use a correction amount and split by sub-meter usage ratio.",
            "Syötä oikaisu ja jaa kulutussuhteella.",
        )
    )
    st.info(
        tr(
            "A true-up is an HSY correction of earlier estimated bills. "
            "Use this page only when HSY sends a correction. "
            "Enter a positive amount for an extra charge or a negative amount for a credit. "
            "Select usage from saved periods or enter it manually. "
            "The amount is split proportionally by sub-meter usage. "
            "If the amount is positive, Ameen reimburses Jussi, since Jussi pays HSY.",
            "Oikaisu on HSY:n korjaus aiemmin arvioituihin laskuihin. "
            "Käytä tätä sivua vain, kun HSY lähettää oikaisun. "
            "Syötä positiivinen summa lisämaksulle tai negatiivinen hyvitykselle. "
            "Valitse kulutus tallennetuista jaksoista tai syötä se manuaalisesti. "
            "Summa jaetaan kulutuksen mukaan. Jos summa on positiivinen, "
            "Ameen maksaa Jussille, koska Jussi maksaa HSY:lle.",
        )
    )

    period_records = get_records("periods")
    if not period_records:
        period_records = local_periods_records()

    st.subheader(tr("True-up details", "Oikaisun tiedot"))
    col_trueup = st.columns(2)
    with col_trueup[0]:
        trueup_start = st.date_input(
            tr("True-up start date", "Oikaisun alkupäivä"),
            format="DD/MM/YYYY",
        )
    with col_trueup[1]:
        trueup_end = st.date_input(
            tr("True-up end date", "Oikaisun loppupäivä"),
            format="DD/MM/YYYY",
        )
    trueup_amount_text = st.text_input(
        tr("True-up amount (€)", "Oikaisun summa (€)"),
        placeholder="0,00€",
    )
    trueup_amount = parse_number(trueup_amount_text)

    usage_source = st.radio(
        tr("Usage source", "Kulutuksen lähde"),
        [tr("Use stored periods", "Käytä tallennettuja jaksoja"), tr("Manual usage", "Manuaalinen kulutus")],
        horizontal=True,
    )
    s1_use_text = None
    s2_use_text = None
    if usage_source == tr("Use stored periods", "Käytä tallennettuja jaksoja"):
        if not period_records:
            st.info(tr("No history entries found.", "Historiaa ei löydy."))
            selected = []
        else:
            options = []
            for idx, entry in enumerate(period_records):
                s1_use = parse_number(entry.get("AS-1 usage")) or 0.0
                s2_use = parse_number(entry.get("AS-2 usage")) or 0.0
                label = (
                    f"{entry.get('Period start')} to {entry.get('Period end')} "
                    f"(AS-1 {format_m3(s1_use)}, "
                    f"AS-2 {format_m3(s2_use)})"
                )
                options.append((idx, label))

            selected_indices = st.multiselect(
                tr("Select periods to cover the true-up", "Valitse jaksot, joita oikaisu koskee"),
                options=options,
                format_func=lambda x: x[1],
            )
            selected = [period_records[idx] for idx, _ in selected_indices]

        s1_use = (
            sum(parse_number(entry.get("AS-1 usage")) or 0.0 for entry in selected)
            if selected
            else 0.0
        )
        s2_use = (
            sum(parse_number(entry.get("AS-2 usage")) or 0.0 for entry in selected)
            if selected
            else 0.0
        )
    else:
        col_manual = st.columns(2)
        with col_manual[0]:
            s1_use_text = st.text_input(
                tr("AS-1 usage (m³)", "AS-1 kulutus (m³)"),
                placeholder="0,000m3",
            )
            s1_use = parse_number(s1_use_text)
        with col_manual[1]:
            s2_use_text = st.text_input(
                tr("AS-2 usage (m³)", "AS-2 kulutus (m³)"),
                placeholder="0,000m3",
            )
            s2_use = parse_number(s2_use_text)

    save_trueup = st.checkbox(tr("Save this true-up", "Tallenna oikaisu"), value=True)
    submitted = st.button(tr("Calculate true-up", "Laske oikaisu"))

    if submitted:
        errors = []
        if trueup_amount is None:
            errors.append(tr("True-up amount is required.", "Oikaisun summa vaaditaan."))
        if s1_use is None:
            errors.append(tr("AS-1 usage is required.", "AS-1 kulutus vaaditaan."))
        if s2_use is None:
            errors.append(tr("AS-2 usage is required.", "AS-2 kulutus vaaditaan."))
        if trueup_amount_text and not validate_decimal_places(trueup_amount_text, 2):
            errors.append(tr("True-up amount must have at most 2 decimals.", "Oikaisun summa enintään 2 desimaalia."))
        if usage_source == tr("Manual usage", "Manuaalinen kulutus"):
            if s1_use_text is not None and not validate_decimal_places(s1_use_text, 3):
                errors.append(tr("AS-1 usage must have at most 3 decimals.", "AS-1 kulutus enintään 3 desimaalia."))
            if s2_use_text is not None and not validate_decimal_places(s2_use_text, 3):
                errors.append(tr("AS-2 usage must have at most 3 decimals.", "AS-2 kulutus enintään 3 desimaalia."))
        if errors:
            for err in errors:
                st.error(err)
        else:
            try:
                trueup = compute_trueup(s1_use, s2_use, trueup_amount)
            except ValueError as exc:
                st.error(tr_error(str(exc)))
            else:
                share_1 = trueup["share_1"]
                share_2 = trueup["share_2"]

            st.success(tr("True-up calculated.", "Oikaisu laskettu."))
            st.markdown(f"### {tr('True-up split', 'Oikaisun jako')}")
            st.table(
                [
                    {
                        tr("Person", "Henkilö"): "AS-1 (Ameen)",
                        tr("Usage", "Kulutus"): format_m3(s1_use),
                        tr("Share", "Osuus"): format_eur(share_1),
                    },
                    {
                        tr("Person", "Henkilö"): "AS-2 (Jussi)",
                        tr("Usage", "Kulutus"): format_m3(s2_use),
                        tr("Share", "Osuus"): format_eur(share_2),
                    },
                ]
            )

            st.markdown(f"### {tr('Who owes whom', 'Kuka maksaa kenelle')}")
            if trueup_amount > 0:
                st.write(
                    {
                        tr("AS-1 (Ameen) owes", "AS-1 (Ameen) maksaa"): format_eur(share_1),
                        tr("AS-2 (Jussi) owes", "AS-2 (Jussi) maksaa"): format_eur(share_2),
                    }
                )
            elif trueup_amount < 0:
                st.write(
                    {
                        tr("AS-1 (Ameen) credit", "AS-1 (Ameen) hyvitys"): format_eur(abs(share_1)),
                        tr("AS-2 (Jussi) credit", "AS-2 (Jussi) hyvitys"): format_eur(abs(share_2)),
                    }
                )
            else:
                st.write(tr("No true-up amount entered.", "Oikaisua ei annettu."))

            if save_trueup:
                record = {
                    "Period start": trueup_start.strftime("%d/%m/%Y"),
                    "Period end": trueup_end.strftime("%d/%m/%Y"),
                    "AS-1 usage": s1_use,
                    "AS-2 usage": s2_use,
                    "True-up amount": trueup_amount,
                    "AS-1 share": share_1,
                    "AS-2 share": share_2,
                    "Saved at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                }
                if get_sheet():
                    if append_record("trueups", TRUEUPS_HEADERS, record):
                        st.success(tr("Saved to history (Google Sheets).", "Tallennettu historiaan (Google Sheets)."))
                    # Error message already shown by append_record if failed
                else:
                    st.warning(
                        tr(
                            "Google Sheets not configured; true-up was not saved.",
                            "Google Sheets ei ole määritetty; oikaisua ei tallennettu.",
                        )
                    )

elif page_id == "history":
    st.header(tr("History", "Historia"))
    st.caption(tr("Saved billing periods and computed shares.", "Tallennetut jaksot ja lasketut osuudet."))
    
    # Cache refresh and clear buttons
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button(tr("🔄 Refresh data", "🔄 Päivitä tiedot"), help=tr("Clear cache and reload from Google Sheets", "Tyhjennä välimuisti ja lataa uudelleen")):
            clear_records_cache()
            st.rerun()
    with btn_col2:
        if st.button(
            tr("🗑️ Clear local history", "🗑️ Tyhjennä paikallinen historia"), 
            help=tr(
                "For development/testing only. If History shows data but Google Sheet is empty, "
                "click this to clear local cache. History should match Google Sheet.",
                "Vain kehitystä/testausta varten. Jos Historia näyttää dataa mutta Google Sheet on tyhjä, "
                "klikkaa tätä tyhjentääksesi paikallisen välimuistin. Historian pitäisi vastata Google Sheetiä."
            )
        ):
            local_file = DATA_DIR / "history.json"
            if local_file.exists():
                local_file.unlink()
                st.success(tr("Local history cleared. History now matches Google Sheet.", "Paikallinen historia tyhjennetty. Historia vastaa nyt Google Sheetiä."))
                st.rerun()
            else:
                st.info(tr("No local history to clear.", "Ei paikallista historiaa tyhjennettäväksi."))
    
    period_records = get_records("periods")
    if not period_records:
        period_records = local_periods_records()
    trueup_records = get_records("trueups")

    if not period_records and not trueup_records:
        st.info(tr("No history entries found.", "Historiaa ei löydy."))
    else:
        periods_tab, trueups_tab = st.tabs(
            [tr("Periods", "Jaksot"), tr("True-ups", "Oikaisut")]
        )

        with periods_tab:
            rows = []
            # Track totals
            total_s1_usage = 0.0
            total_s2_usage = 0.0
            total_basic_fees = 0.0
            total_usage_fees = 0.0
            total_s1_total = 0.0
            total_s2_total = 0.0
            
            for record in period_records:
                data = normalize_period_record(record)
                # Accumulate totals
                total_s1_usage += data["s1_use"] or 0
                total_s2_usage += data["s2_use"] or 0
                total_basic_fees += data["basic_fees"] or 0
                total_usage_fees += data["usage_fees"] or 0
                total_s1_total += data["total_1"] or 0
                total_s2_total += data["total_2"] or 0
                
                rows.append(
                    {
                        tr("Period start", "Jakson alku"): format_date(data["period_start"]),
                        tr("Period end", "Jakson loppu"): format_date(data["period_end"]),
                        tr("Invoice", "Lasku"): data.get("invoice_number") or "-",
                        tr("Est. water", "Arvio vesi"): format_m3(data.get("estimated_water")) if data.get("estimated_water") else "-",
                        tr("Due date", "Eräpäivä"): format_date(data.get("due_date")) if data.get("due_date") else "-",
                        tr("Reading start", "Lukema alku"): data.get("reading_start"),
                        tr("Reading end", "Lukema loppu"): data.get("reading_end"),
                        tr("Main usage", "Päämittari"): format_m3(data.get("main_use")) if data.get("main_use") else "-",
                        tr("AS-1 usage", "AS-1 kulutus"): format_m3(data["s1_use"]),
                        tr("AS-2 usage", "AS-2 kulutus"): format_m3(data["s2_use"]),
                        tr("Basic fees", "Perusmaksu"): format_eur(data["basic_fees"]),
                        tr("Usage fees", "Käyttömaksu"): format_eur(data["usage_fees"]),
                        tr("AS-1 total", "AS-1 yhteensä"): format_eur(data["total_1"]),
                        tr("AS-2 total", "AS-2 yhteensä"): format_eur(data["total_2"]),
                        tr("Saved at", "Tallennettu"): format_date(data["saved_at"]),
                    }
                )
            # Add totals row to the table
            if rows:
                totals_row = {
                    tr("Period start", "Jakson alku"): tr("**TOTAL**", "**YHTEENSÄ**"),
                    tr("Period end", "Jakson loppu"): "",
                    tr("Invoice", "Lasku"): "",
                    tr("Est. water", "Arvio vesi"): "",
                    tr("Due date", "Eräpäivä"): "",
                    tr("Reading start", "Lukema alku"): "",
                    tr("Reading end", "Lukema loppu"): "",
                    tr("Main usage", "Päämittari"): "",
                    tr("AS-1 usage", "AS-1 kulutus"): format_m3(total_s1_usage),
                    tr("AS-2 usage", "AS-2 kulutus"): format_m3(total_s2_usage),
                    tr("Basic fees", "Perusmaksu"): format_eur(total_basic_fees),
                    tr("Usage fees", "Käyttömaksu"): format_eur(total_usage_fees),
                    tr("AS-1 total", "AS-1 yhteensä"): format_eur(total_s1_total),
                    tr("AS-2 total", "AS-2 yhteensä"): format_eur(total_s2_total),
                    tr("Saved at", "Tallennettu"): "",
                }
                rows.append(totals_row)
            
            st.dataframe(rows, width="stretch")
            
            # Display cumulative totals breakdown
            if rows and len(rows) > 1:
                st.subheader(tr("Cumulative Totals", "Kumulatiiviset summat"))
                tot_col1, tot_col2, tot_col3 = st.columns(3)
                with tot_col1:
                    st.markdown(f"**AS-1 (Ameen)**")
                    st.markdown(f"- {tr('Total usage', 'Kokonaiskulutus')}: **{format_m3(total_s1_usage)}**")
                    st.markdown(f"- {tr('Basic fees (50%)', 'Perusmaksut (50%)')}: **{format_eur(total_basic_fees / 2)}**")
                    st.markdown(f"- {tr('Usage fees', 'Käyttömaksut')}: **{format_eur(total_s1_total - total_basic_fees / 2)}**")
                    st.markdown(f"- {tr('**Grand total**', '**Kokonaissumma**')}: **{format_eur(total_s1_total)}**")
                with tot_col2:
                    st.markdown(f"**AS-2 (Jussi)**")
                    st.markdown(f"- {tr('Total usage', 'Kokonaiskulutus')}: **{format_m3(total_s2_usage)}**")
                    st.markdown(f"- {tr('Basic fees (50%)', 'Perusmaksut (50%)')}: **{format_eur(total_basic_fees / 2)}**")
                    st.markdown(f"- {tr('Usage fees', 'Käyttömaksut')}: **{format_eur(total_s2_total - total_basic_fees / 2)}**")
                    st.markdown(f"- {tr('**Grand total**', '**Kokonaissumma**')}: **{format_eur(total_s2_total)}**")
                with tot_col3:
                    st.markdown(f"**{tr('Combined', 'Yhteensa')}**")
                    st.markdown(f"- {tr('Total usage', 'Kokonaiskulutus')}: **{format_m3(total_s1_usage + total_s2_usage)}**")
                    st.markdown(f"- {tr('Basic fees', 'Perusmaksut')}: **{format_eur(total_basic_fees)}**")
                    st.markdown(f"- {tr('Usage fees', 'Käyttömaksut')}: **{format_eur(total_usage_fees)}**")
                    st.markdown(f"- {tr('**Grand total**', '**Kokonaissumma**')}: **{format_eur(total_s1_total + total_s2_total)}**")
                
                st.markdown("---")
                st.markdown(f"**{tr('Number of periods', 'Jaksojen määrä')}**: {len(rows) - 1}")
            if rows:
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                period_start = format_date(rows[0].get(tr("Period start", "Jakson alku")))
                period_end = format_date(rows[-1].get(tr("Period end", "Jakson loppu")))
                safe_start = (period_start or "start").replace("/", "-")
                safe_end = (period_end or "end").replace("/", "-")
                csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
                st.download_button(
                    tr("Download history as CSV", "Lataa historia CSV-muodossa"),
                    data=csv_bytes,
                    file_name=f"water_bill_history_{safe_start}_{safe_end}.csv",
                    mime="text/csv",
                )
                pdf_lines = [tr("Water Bill History - Hirvensarvi 16 B", "Vesilaskuhistoria - Hirvensarvi 16 B"), ""]
                
                # Add each period (excluding the totals row for individual listing)
                data_rows = rows[:-1] if len(rows) > 1 else rows
                for i, row in enumerate(data_rows):
                    pdf_lines.append(f"{tr('Period', 'Jakso')} {i+1}: {row.get(tr('Period start', 'Jakson alku'))} - {row.get(tr('Period end', 'Jakson loppu'))}")
                    invoice_val = row.get(tr('Invoice', 'Lasku')) or "-"
                    est_water_val = row.get(tr('Est. water', 'Arvio vesi')) or "-"
                    due_date_val = row.get(tr('Due date', 'Eräpäivä')) or "-"
                    pdf_lines.append(f"  {tr('Invoice', 'Lasku')}: {invoice_val}, {tr('Est. water', 'Arvio vesi')}: {est_water_val}, {tr('Due date', 'Eräpäivä')}: {due_date_val}")
                    pdf_lines.append(f"  {tr('AS-1 usage', 'AS-1 kulutus')}: {row.get(tr('AS-1 usage', 'AS-1 kulutus'))}, {tr('AS-2 usage', 'AS-2 kulutus')}: {row.get(tr('AS-2 usage', 'AS-2 kulutus'))}")
                    pdf_lines.append(f"  {tr('Basic fees', 'Perusmaksu')}: {row.get(tr('Basic fees', 'Perusmaksu'))}, {tr('Usage fees', 'Käyttömaksu')}: {row.get(tr('Usage fees', 'Käyttömaksu'))}")
                    pdf_lines.append(f"  {tr('AS-1 total', 'AS-1 yhteensä')}: {row.get(tr('AS-1 total', 'AS-1 yhteensä'))}, {tr('AS-2 total', 'AS-2 yhteensä')}: {row.get(tr('AS-2 total', 'AS-2 yhteensä'))}")
                    pdf_lines.append("")
                
                # Add cumulative totals summary
                pdf_lines.append("=" * 50)
                pdf_lines.append(tr("CUMULATIVE TOTALS", "KUMULATIIVISET SUMMAT"))
                pdf_lines.append("=" * 50)
                pdf_lines.append("")
                pdf_lines.append(f"AS-1 (Ameen):")
                pdf_lines.append(f"  {tr('Total usage', 'Kokonaiskulutus')}: {format_m3(total_s1_usage)}")
                pdf_lines.append(f"  {tr('Basic fees (50%)', 'Perusmaksut (50%)')}: {format_eur(total_basic_fees / 2)}")
                pdf_lines.append(f"  {tr('Usage fees', 'Käyttömaksut')}: {format_eur(total_s1_total - total_basic_fees / 2)}")
                pdf_lines.append(f"  {tr('Grand total', 'Kokonaissumma')}: {format_eur(total_s1_total)}")
                pdf_lines.append("")
                pdf_lines.append(f"AS-2 (Jussi):")
                pdf_lines.append(f"  {tr('Total usage', 'Kokonaiskulutus')}: {format_m3(total_s2_usage)}")
                pdf_lines.append(f"  {tr('Basic fees (50%)', 'Perusmaksut (50%)')}: {format_eur(total_basic_fees / 2)}")
                pdf_lines.append(f"  {tr('Usage fees', 'Käyttömaksut')}: {format_eur(total_s2_total - total_basic_fees / 2)}")
                pdf_lines.append(f"  {tr('Grand total', 'Kokonaissumma')}: {format_eur(total_s2_total)}")
                pdf_lines.append("")
                pdf_lines.append(f"{tr('Combined', 'Yhteensa')}:")
                pdf_lines.append(f"  {tr('Total usage', 'Kokonaiskulutus')}: {format_m3(total_s1_usage + total_s2_usage)}")
                pdf_lines.append(f"  {tr('Basic fees', 'Perusmaksut')}: {format_eur(total_basic_fees)}")
                pdf_lines.append(f"  {tr('Usage fees', 'Käyttömaksut')}: {format_eur(total_usage_fees)}")
                pdf_lines.append(f"  {tr('Grand total', 'Kokonaissumma')}: {format_eur(total_s1_total + total_s2_total)}")
                pdf_lines.append("")
                pdf_lines.append("-" * 50)
                pdf_lines.append(f"{tr('Number of periods', 'Jaksojen määrä')}: {len(data_rows)}")
                pdf_lines.append("")
                
                pdf_lines = wrap_lines(pdf_lines)
                pdf_data = build_simple_pdf(pdf_lines)
                st.download_button(
                    tr("Download history as PDF", "Lataa historia PDF-muodossa"),
                    data=pdf_data,
                    file_name=f"water_bill_history_{safe_start}_{safe_end}.pdf",
                    mime="application/pdf",
                )

        with trueups_tab:
            rows = []
            for record in trueup_records:
                data = normalize_trueup_record(record)
                rows.append(
                    {
                        tr("Period start", "Jakson alku"): format_date(data["period_start"]),
                        tr("Period end", "Jakson loppu"): format_date(data["period_end"]),
                        tr("AS-1 usage", "AS-1 kulutus"): format_m3(data["s1_use"]),
                        tr("AS-2 usage", "AS-2 kulutus"): format_m3(data["s2_use"]),
                        tr("True-up amount", "Oikaisun summa"): format_eur(data["trueup_amount"]),
                        tr("AS-1 share", "AS-1 osuus"): format_eur(data["share_1"]),
                        tr("AS-2 share", "AS-2 osuus"): format_eur(data["share_2"]),
                        tr("Saved at", "Tallennettu"): format_date(data["saved_at"]),
                    }
                )
            if rows:
                st.dataframe(rows, width="stretch")
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                period_start = format_date(rows[0].get(tr("Period start", "Jakson alku")))
                period_end = format_date(rows[-1].get(tr("Period end", "Jakson loppu")))
                safe_start = (period_start or "start").replace("/", "-")
                safe_end = (period_end or "end").replace("/", "-")
                csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
                st.download_button(
                    tr("Download true-ups as CSV", "Lataa oikaisut CSV-muodossa"),
                    data=csv_bytes,
                    file_name=f"water_bill_trueups_{safe_start}_{safe_end}.csv",
                    mime="text/csv",
                )
                pdf_lines = [tr("True-ups Export", "Oikaisut - vienti"), ""]
                for row in rows:
                    pdf_lines.append(
                        f"{tr('Period', 'Jakso')}: {row.get(tr('Period start', 'Jakson alku'))} "
                        f"- {row.get(tr('Period end', 'Jakson loppu'))}"
                    )
                    pdf_lines.append(
                        f"{tr('AS-1 usage', 'AS-1 kulutus')}: {row.get(tr('AS-1 usage', 'AS-1 kulutus'))}, "
                        f"{tr('AS-2 usage', 'AS-2 kulutus')}: {row.get(tr('AS-2 usage', 'AS-2 kulutus'))}"
                    )
                    pdf_lines.append(
                        f"{tr('True-up amount', 'Oikaisun summa')}: {row.get(tr('True-up amount', 'Oikaisun summa'))}"
                    )
                    pdf_lines.append(
                        f"{tr('AS-1 share', 'AS-1 osuus')}: {row.get(tr('AS-1 share', 'AS-1 osuus'))}, "
                        f"{tr('AS-2 share', 'AS-2 osuus')}: {row.get(tr('AS-2 share', 'AS-2 osuus'))}"
                    )
                    pdf_lines.append("")
                pdf_lines = wrap_lines(pdf_lines)
                pdf_data = build_simple_pdf(pdf_lines)
                st.download_button(
                    tr("Download true-ups as PDF", "Lataa oikaisut PDF-muodossa"),
                    data=pdf_data,
                    file_name=f"water_bill_trueups_{safe_start}_{safe_end}.pdf",
                    mime="application/pdf",
                )
            else:
                st.info(tr("No true-ups found.", "Oikaisuja ei löydy."))
