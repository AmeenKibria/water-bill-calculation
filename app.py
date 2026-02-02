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
def hash_password(password: str) -> str:
    """Hash password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


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
                st.rerun()
            else:
                st.error("Invalid username or password")
    
    return False


def logout():
    """Log out the current user."""
    st.session_state.authenticated = False
    st.session_state.username = None
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


def get_records(tab_name: str) -> list[dict]:
    sheet = get_sheet()
    if sheet is None:
        return []
    worksheet = sheet.worksheet(tab_name)
    return worksheet.get_all_records()


def append_record(tab_name: str, headers: list[str], record: dict):
    sheet = get_sheet()
    if sheet is None:
        return
    worksheet = sheet.worksheet(tab_name)
    row = [record.get(header, "") for header in headers]
    worksheet.append_row(row, value_input_option="USER_ENTERED")


def local_periods_records() -> list[dict]:
    records = []
    for entry in load_local_history():
        records.append(
            {
                "Period start": format_date(entry.get("period_start")),
                "Period end": format_date(entry.get("period_end")),
                "Reading start": entry.get("reading_start"),
                "Reading end": entry.get("reading_end"),
                "AS-1 usage": entry.get("s1_use"),
                "AS-2 usage": entry.get("s2_use"),
                "AS-1 adjusted": entry.get("adj_s1_use"),
                "AS-2 adjusted": entry.get("adj_s2_use"),
                "Basic fees": entry.get("basic_fees"),
                "Usage fees": entry.get("usage_fees"),
                "AS-1 total": entry.get("total_1"),
                "AS-2 total": entry.get("total_2"),
                "Mismatch policy": entry.get("mismatch_policy"),
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
            "Perusmaksu jaetaan 50/50. Kayttomaksu jaetaan kulutuksen mukaan.",
        )
    )
    with st.expander(tr("How this works", "Miten tama toimii")):
        st.markdown(
            tr(
                "- Basic fees are split 50/50.\n"
                "- Usage fees are split by sub-meter usage ratio.\n"
                "- Mismatch is shown for awareness; default is display-only.\n"
                "- You can manually override mismatch allocation if needed.",
                "- Perusmaksu jaetaan 50/50.\n"
                "- Kayttomaksu jaetaan alamittarien kulutussuhteessa.\n"
                "- Poikkeama naytetaan; oletus on vain naytto.\n"
                "- Poikkeaman jaon voi tarvittaessa yliajaa.",
            )
        )

    st.subheader(tr("Main meter (optional, for mismatch display)", "Paamittari (valinnainen, poikkeaman nayttoon)"))
    with st.expander(
        tr("Add main meter readings", "Lisaa paamittarin lukemat"),
        expanded=True,
    ):
        st.caption(
            tr(
                "Optional: enter the main meter start/end to show mismatch.",
                "Valinnainen: anna paamittarin alku/loppu poikkeaman nayttoon.",
            )
        )
        use_main = st.checkbox(
            tr("Provide main meter readings", "Anna paamittarin lukemat"),
            value=False,
        )
        if use_main:
            main_start = st.number_input(
                tr("Main start (m3)", "Paamittari alku (m3)"),
                min_value=0.0,
                value=0.0,
                step=None,
                format="%.3f",
            )
            main_end = st.number_input(
                tr("Main end (m3)", "Paamittari loppu (m3)"),
                min_value=0.0,
                value=0.0,
                step=None,
                format="%.3f",
            )
        else:
            main_start = None
            main_end = None

    st.subheader(tr("Billing period", "Laskutusjakso"))
    col_period = st.columns(2)
    with col_period[0]:
        period_start = st.date_input(
            tr("Start date", "Alkupaiva"),
            format="DD/MM/YYYY",
        )
    with col_period[1]:
        period_end = st.date_input(
            tr("End date", "Loppupaiva"),
            format="DD/MM/YYYY",
        )

    st.subheader(tr("Totals bill", "Laskun summat"))
    basic_fees_text = st.text_input(
        tr("Basic fees total", "Perusmaksut yhteensa"),
        placeholder="0,00€",
    )
    usage_fees_text = st.text_input(
        tr("Consumption total", "Kayttomaksut yhteensa"),
        placeholder="0,00€",
    )
    basic_fees = parse_number(basic_fees_text)
    usage_fees = parse_number(usage_fees_text)

    st.subheader(tr("Sub-meter usage (m³)", "Alamittarien kulutus (m³)"))
    st.caption(
        tr(
            "Optional: record when the start/end readings were taken.",
            "Valinnainen: merkitse milloin alku- ja loppulukemat otettiin.",
        )
    )
    usage_mode = st.radio(
        tr("Input method", "Syottotapa"),
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

    row1_cols = st.columns([0.9, 0.6, 1.2, 1.2])
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
            s1_start_text = st.text_input(
                tr("AS-1 start (Ameen)", "AS-1 alku (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s1_start_text",
            )
            if s1_start_text and not validate_decimal_places(s1_start_text, 3):
                st.error(tr("AS-1 start: max 3 decimals.", "AS-1 alku: enintaan 3 desimaalia."))
        else:
            s1_use_text = st.text_input(
                tr("AS-1 usage (Ameen)", "AS-1 kulutus (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s1_use_text",
            )
            if s1_use_text and not validate_decimal_places(s1_use_text, 3):
                st.error(tr("AS-1 usage: max 3 decimals.", "AS-1 kulutus: enintaan 3 desimaalia."))
    with row1_cols[3]:
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            s2_start_text = st.text_input(
                tr("AS-2 start (Jussi)", "AS-2 alku (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s2_start_text",
            )
            if s2_start_text and not validate_decimal_places(s2_start_text, 3):
                st.error(tr("AS-2 start: max 3 decimals.", "AS-2 alku: enintaan 3 desimaalia."))
        else:
            s2_use_text = st.text_input(
                tr("AS-2 usage (Jussi)", "AS-2 kulutus (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s2_use_text",
            )
            if s2_use_text and not validate_decimal_places(s2_use_text, 3):
                st.error(tr("AS-2 usage: max 3 decimals.", "AS-2 kulutus: enintaan 3 desimaalia."))

    row2_cols = st.columns([0.9, 0.6, 1.2, 1.2])
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
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            s1_end_text = st.text_input(
                tr("AS-1 end (Ameen)", "AS-1 loppu (Ameen)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s1_end_text",
            )
            if s1_end_text and not validate_decimal_places(s1_end_text, 3):
                st.error(tr("AS-1 end: max 3 decimals.", "AS-1 loppu: enintaan 3 desimaalia."))
            s1_start = parse_number(s1_start_text)
            s1_end = parse_number(s1_end_text)
            s1_use = (
                s1_end - s1_start
                if s1_start is not None and s1_end is not None
                else None
            )
        else:
            s1_use = parse_number(s1_use_text)
            s1_start = None
            s1_end = None
    with row2_cols[3]:
        if usage_mode == tr("Readings (start/end)", "Lukemat (alku/loppu)"):
            s2_end_text = st.text_input(
                tr("AS-2 end (Jussi)", "AS-2 loppu (Jussi)"),
                placeholder="0,000m3",
                help=tr("Use comma as decimal. Max 3 decimals.", "Kayta pilkkua. Enintaan 3 desimaalia."),
                key="s2_end_text",
            )
            if s2_end_text and not validate_decimal_places(s2_end_text, 3):
                st.error(tr("AS-2 end: max 3 decimals.", "AS-2 loppu: enintaan 3 desimaalia."))
            s2_start = parse_number(s2_start_text)
            s2_end = parse_number(s2_end_text)
            s2_use = (
                s2_end - s2_start
                if s2_start is not None and s2_end is not None
                else None
            )
        else:
            s2_use = parse_number(s2_use_text)
            s2_start = None
            s2_end = None

    st.subheader(tr("Mismatch allocation (manual override)", "Poikkeaman jako (manuaalinen yliajo)"))
    policy_ignore = tr("Ignore mismatch (display-only)", "Ohita poikkeama (vain naytto)")
    policy_half = tr("Split mismatch 50/50", "Jaa poikkeama 50/50")
    policy_prop = tr("Split mismatch proportional", "Jaa poikkeama suhteessa")
    mismatch_policy_label = st.selectbox(
        tr("Policy", "Kaytanto"),
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
                "Oletus on vain naytto. Yliajo toimii vain, jos paamittarin "
                "Valinnainen: miten jaetaan paamittarin ja alamittarien erotus.\n"
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
            errors.append(tr("Basic fees total is required.", "Perusmaksut yhteensa vaaditaan."))
        if usage_fees is None:
            errors.append(tr("Consumption total is required.", "Kayttomaksut yhteensa vaaditaan."))
        if not validate_decimal_places(basic_fees_text, 2):
            errors.append(tr("Basic fees must have at most 2 decimals.", "Perusmaksu enintaan 2 desimaalia."))
        if not validate_decimal_places(usage_fees_text, 2):
            errors.append(tr("Consumption total must have at most 2 decimals.", "Kayttomaksu enintaan 2 desimaalia."))
        if s1_start is not None and not validate_decimal_places(s1_start_text, 3):
            errors.append(tr("AS-1 start must have at most 3 decimals.", "AS-1 alku enintaan 3 desimaalia."))
        if s1_end is not None and not validate_decimal_places(s1_end_text, 3):
            errors.append(tr("AS-1 end must have at most 3 decimals.", "AS-1 loppu enintaan 3 desimaalia."))
        if s2_start is not None and not validate_decimal_places(s2_start_text, 3):
            errors.append(tr("AS-2 start must have at most 3 decimals.", "AS-2 alku enintaan 3 desimaalia."))
        if s2_end is not None and not validate_decimal_places(s2_end_text, 3):
            errors.append(tr("AS-2 end must have at most 3 decimals.", "AS-2 loppu enintaan 3 desimaalia."))
        if usage_mode == tr("Usage only", "Vain kulutus"):
            if s1_use_text is not None and not validate_decimal_places(s1_use_text, 3):
                errors.append(tr("AS-1 usage must have at most 3 decimals.", "AS-1 kulutus enintaan 3 desimaalia."))
            if s2_use_text is not None and not validate_decimal_places(s2_use_text, 3):
                errors.append(tr("AS-2 usage must have at most 3 decimals.", "AS-2 kulutus enintaan 3 desimaalia."))
        if s1_use < 0 or s2_use < 0:
            errors.append(tr("Sub-meter usage cannot be negative.", "Alamittarin kulutus ei voi olla negatiivinen."))
        if use_main and main_start is not None and main_end is not None:
            if main_end <= main_start:
                errors.append(tr("Main meter usage must be greater than 0.", "Paamittarin kulutuksen on oltava suurempi kuin 0."))
        if s1_use + s2_use <= 0:
            errors.append(tr("Total sub-meter usage must be greater than 0.", "Alamittarien kokonaiskulutuksen on oltava suurempi kuin 0."))
        if (
            mismatch_policy_label != policy_ignore
            and not use_main
        ):
            errors.append(tr("Mismatch override requires main meter readings.", "Poikkeaman yliajo vaatii paamittarin lukemat."))

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
                st.error(str(exc))
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
                        tr("Person", "Henkilo"): "AS-1 (Ameen)",
                        tr("Usage", "Kulutus"): format_m3(adj_s1_use),
                        tr("Usage fees", "Kayttomaksu"): format_eur(usage_share_1),
                        tr("Basic fees", "Perusmaksu"): format_eur(basic_share),
                        tr("Total", "Yhteensa"): format_eur(basic_share + usage_share_1),
                    },
                    {
                        tr("Person", "Henkilo"): "AS-2 (Jussi)",
                        tr("Usage", "Kulutus"): format_m3(adj_s2_use),
                        tr("Usage fees", "Kayttomaksu"): format_eur(usage_share_2),
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
                    tr("Total for Ameen", "Ameen yhteensa"): format_eur(total_ameen),
                    tr("Total for Jussi", "Jussi yhteensa"): format_eur(total_jussi),
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
                    "Talla jaksolla: Jussi yhteensa "
                    f"{format_eur(total_jussi)}, Ameen yhteensa "
                    f"{format_eur(total_ameen)} → Ameen maksaa Jussille "
                    f"{format_eur(total_ameen)}."
                )
            )
            st.text_input(
                tr("WhatsApp copy/paste", "WhatsApp kopioi/liita"),
                value=whatsapp_line,
            )

            st.markdown(f"### {tr('Mismatch (always shown)', 'Poikkeama (aina naytetaan)')}")
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
                        tr("Main usage", "Paamittarin kulutus"): format_m3(main_use),
                        tr("Sub-meter total", "Alamittarit yhteensa"): format_m3(sub_sum),
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
                st.info(tr("Mismatch not available (main meter not provided).", "Poikkeamaa ei voi laskea (paamittari puuttuu)."))

            reading_start = f"{reading_start_date.strftime('%d/%m/%Y')} {reading_start_time.strftime('%H:%M')}"
            reading_end = f"{reading_end_date.strftime('%d/%m/%Y')} {reading_end_time.strftime('%H:%M')}"

            if save_entry:
                record = {
                    "Period start": period_start.strftime("%d/%m/%Y"),
                    "Period end": period_end.strftime("%d/%m/%Y"),
                    "Reading start": reading_start,
                    "Reading end": reading_end,
                    "AS-1 usage": s1_use,
                    "AS-2 usage": s2_use,
                    "AS-1 adjusted": adj_s1_use,
                    "AS-2 adjusted": adj_s2_use,
                    "Basic fees": basic_fees,
                    "Usage fees": usage_fees,
                    "AS-1 total": basic_share + usage_share_1,
                    "AS-2 total": basic_share + usage_share_2,
                    "Mismatch policy": mismatch_policy,
                    "Mismatch (m3)": mismatch_m3,
                    "Mismatch (%)": mismatch_pct,
                    "Saved at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                }
                if get_sheet():
                    append_record("periods", PERIODS_HEADERS, record)
                    st.success(tr("Saved to history.", "Tallennettu historiaan."))
                else:
                    history = load_local_history()
                    history.append(
                        {
                            "period_start": str(period_start),
                            "period_end": str(period_end),
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
            "Syota oikaisu ja jaa kulutussuhteella.",
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
            "Kayta tata sivua vain, kun HSY lahettaa oikaisun. "
            "Syota positiivinen summa lisamaksulle tai negatiivinen hyvitykselle. "
            "Valitse kulutus tallennetuista jaksoista tai syota se manuaalisesti. "
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
            tr("True-up start date", "Oikaisun alkupaiva"),
            format="DD/MM/YYYY",
        )
    with col_trueup[1]:
        trueup_end = st.date_input(
            tr("True-up end date", "Oikaisun loppupaiva"),
            format="DD/MM/YYYY",
        )
    trueup_amount_text = st.text_input(
        tr("True-up amount (€)", "Oikaisun summa (€)"),
        placeholder="0,00€",
    )
    trueup_amount = parse_number(trueup_amount_text)

    usage_source = st.radio(
        tr("Usage source", "Kulutuksen lahde"),
        [tr("Use stored periods", "Kayta tallennettuja jaksoja"), tr("Manual usage", "Manuaalinen kulutus")],
        horizontal=True,
    )
    s1_use_text = None
    s2_use_text = None
    if usage_source == tr("Use stored periods", "Kayta tallennettuja jaksoja"):
        if not period_records:
            st.info(tr("No history entries found.", "Historiaa ei loydy."))
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
            errors.append(tr("True-up amount must have at most 2 decimals.", "Oikaisun summa enintaan 2 desimaalia."))
        if usage_source == tr("Manual usage", "Manuaalinen kulutus"):
            if s1_use_text is not None and not validate_decimal_places(s1_use_text, 3):
                errors.append(tr("AS-1 usage must have at most 3 decimals.", "AS-1 kulutus enintaan 3 desimaalia."))
            if s2_use_text is not None and not validate_decimal_places(s2_use_text, 3):
                errors.append(tr("AS-2 usage must have at most 3 decimals.", "AS-2 kulutus enintaan 3 desimaalia."))
        if errors:
            for err in errors:
                st.error(err)
        else:
            try:
                trueup = compute_trueup(s1_use, s2_use, trueup_amount)
            except ValueError as exc:
                st.error(tr(str(exc), str(exc)))
            else:
                share_1 = trueup["share_1"]
                share_2 = trueup["share_2"]

            st.success(tr("True-up calculated.", "Oikaisu laskettu."))
            st.markdown(f"### {tr('True-up split', 'Oikaisun jako')}")
            st.table(
                [
                    {
                        tr("Person", "Henkilo"): "AS-1 (Ameen)",
                        tr("Usage", "Kulutus"): format_m3(s1_use),
                        tr("Share", "Osuus"): format_eur(share_1),
                    },
                    {
                        tr("Person", "Henkilo"): "AS-2 (Jussi)",
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
                    append_record("trueups", TRUEUPS_HEADERS, record)
                    st.success(tr("Saved to history.", "Tallennettu historiaan."))
                else:
                    st.warning(
                        tr(
                            "Sheets not configured; true-up was not saved.",
                            "Sheets ei ole konfiguroitu; oikaisua ei tallennettu.",
                        )
                    )

elif page_id == "history":
    st.header(tr("History", "Historia"))
    st.caption(tr("Saved billing periods and computed shares.", "Tallennetut jaksot ja lasketut osuudet."))
    period_records = get_records("periods")
    if not period_records:
        period_records = local_periods_records()
    trueup_records = get_records("trueups")

    if not period_records and not trueup_records:
        st.info(tr("No history entries found.", "Historiaa ei loydy."))
    else:
        periods_tab, trueups_tab = st.tabs(
            [tr("Periods", "Jaksot"), tr("True-ups", "Oikaisut")]
        )

        with periods_tab:
            rows = []
            for record in period_records:
                data = normalize_period_record(record)
                rows.append(
                    {
                        tr("Period start", "Jakson alku"): format_date(data["period_start"]),
                        tr("Period end", "Jakson loppu"): format_date(data["period_end"]),
                        tr("Reading start", "Lukema alku"): data.get("reading_start"),
                        tr("Reading end", "Lukema loppu"): data.get("reading_end"),
                        tr("AS-1 usage", "AS-1 kulutus"): format_m3(data["s1_use"]),
                        tr("AS-2 usage", "AS-2 kulutus"): format_m3(data["s2_use"]),
                        tr("AS-1 adjusted", "AS-1 sovitettu"): format_m3(data["adj_s1_use"]),
                        tr("AS-2 adjusted", "AS-2 sovitettu"): format_m3(data["adj_s2_use"]),
                        tr("Basic fees", "Perusmaksu"): format_eur(data["basic_fees"]),
                        tr("Usage fees", "Kayttomaksu"): format_eur(data["usage_fees"]),
                        tr("AS-1 total", "AS-1 yhteensa"): format_eur(data["total_1"]),
                        tr("AS-2 total", "AS-2 yhteensa"): format_eur(data["total_2"]),
                        tr("Mismatch policy", "Poikkeaman kaytanto"): data["mismatch_policy"],
                        tr("Mismatch (m³)", "Poikkeama (m³)"): (
                            format_m3(data["mismatch_m3"])
                            if data["mismatch_m3"] is not None
                            else None
                        ),
                        tr("Mismatch (%)", "Poikkeama (%)"): (
                            f"{format_number(data['mismatch_pct'] * 100, 2)}%"
                            if data["mismatch_pct"] is not None
                            else None
                        ),
                        tr("Saved at", "Tallennettu"): format_date(data["saved_at"]),
                    }
                )
            st.dataframe(rows, width="stretch")
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
                pdf_lines = [tr("History Export", "Historia - vienti"), ""]
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
                        f"{tr('Basic fees', 'Perusmaksu')}: {row.get(tr('Basic fees', 'Perusmaksu'))}, "
                        f"{tr('Usage fees', 'Kayttomaksu')}: {row.get(tr('Usage fees', 'Kayttomaksu'))}"
                    )
                    pdf_lines.append(
                        f"{tr('AS-1 total', 'AS-1 yhteensa')}: {row.get(tr('AS-1 total', 'AS-1 yhteensa'))}, "
                        f"{tr('AS-2 total', 'AS-2 yhteensa')}: {row.get(tr('AS-2 total', 'AS-2 yhteensa'))}"
                    )
                    pdf_lines.append(
                        f"{tr('Mismatch policy', 'Poikkeaman kaytanto')}: "
                        f"{row.get(tr('Mismatch policy', 'Poikkeaman kaytanto'))}"
                    )
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
                st.info(tr("No true-ups found.", "Oikaisuja ei loydy."))
