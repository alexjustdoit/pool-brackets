"""
Pool Brackets — Tournament bracket manager for pool/billiards.
"""

import os
import io
import csv
import streamlit as st
from supabase import Client

from utils.db import (
    get_client,
    get_all_players,
    get_or_create_player,
    get_all_tournaments,
    get_tournament,
    create_tournament,
    get_competitors,
    add_competitor,
    remove_competitor,
    toggle_paid,
    check_doubles_eligibility,
    start_tournament,
    get_bracket_data,
    organize_bracket_for_render,
    record_result,
    change_result,
    import_historical,
)
from utils.bracket import render_bracket_html
from utils.names import normalize_name, validate_name_format, find_similar_names
from utils.stats import get_player_stats, get_leaderboard

st.set_page_config(
    page_title="Pool Brackets",
    page_icon="🎱",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { max-width: 100%; }
[data-testid="collapsedControl"] { display: none; }
div[data-testid="column"] { min-width: 0 !important; }

/* Hide default sidebar toggle */
section[data-testid="stSidebar"] { display: none; }

/* Tap-friendly buttons */
.stButton > button {
    min-height: 44px;
    font-size: 15px;
    border-radius: 8px;
    width: 100%;
}

/* Primary action button */
.stButton > button[kind="primary"] {
    background: #F59E0B;
    color: #111;
    border: none;
    font-weight: 700;
}
.stButton > button[kind="primary"]:hover { background: #D97706; }

/* Tournament card */
.t-card {
    background: #1F2937;
    border: 1px solid #374151;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 10px;
    cursor: pointer;
}
.t-card-active { border-color: #F59E0B; }
.t-card-title { font-weight: 700; font-size: 16px; color: #F9FAFB; margin-bottom: 4px; }
.t-card-meta  { font-size: 13px; color: #9CA3AF; }

/* Status pills */
.pill {
    display: inline-block; padding: 2px 10px;
    border-radius: 20px; font-size: 12px; font-weight: 600;
}
.pill-active    { background: rgba(245,158,11,.18); color: #F59E0B; }
.pill-setup     { background: rgba(99,102,241,.18);  color: #818CF8; }
.pill-completed { background: rgba(34,197,94,.18);   color: #4ADE80; }

/* Metric cards */
[data-testid="stMetric"] {
    background: #1F2937;
    border-radius: 8px;
    padding: 12px 16px !important;
    border: 1px solid #374151;
}

/* Section header */
.sh {
    font-size: 11px; font-weight: 700; letter-spacing: .08em;
    text-transform: uppercase; color: #6B7280;
    margin: 20px 0 8px; padding-bottom: 6px;
    border-bottom: 1px solid #2D3748;
}

/* Match entry card */
.match-entry {
    background: #1F2937;
    border: 1.5px solid #F59E0B;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 10px;
}
.match-entry-label {
    font-size: 12px; color: #9CA3AF; margin-bottom: 6px; font-weight: 600;
}

/* Paid toggle colors */
.paid-yes { color: #4ADE80; font-weight: 700; }
.paid-no  { color: #EF4444; font-weight: 700; }

/* Recent form chars */
.fw { color: #4ADE80; font-weight: 700; display: inline-block; width: 18px; text-align: center; }
.fl { color: #EF4444; font-weight: 700; display: inline-block; width: 18px; text-align: center; }

/* Back nav */
.back-btn { color: #9CA3AF; font-size: 13px; cursor: pointer; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _sb() -> Client:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        st.error("Missing SUPABASE_URL / SUPABASE_KEY")
        st.stop()
    return get_client(url, key)


sb = _sb()


# ──────────────────────────────────────────────────────────────────────────────
# Session state
# ──────────────────────────────────────────────────────────────────────────────

def _init():
    for k, v in {
        "view": "home",           # home | tournament | stats | import
        "tournament_id": None,
        "editing_match": None,    # match_id being corrected
        "selected_match_id": None,  # ready match tapped in bracket
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init()


def _go(view: str, tournament_id: str | None = None):
    st.session_state.view = view
    if tournament_id is not None:
        st.session_state.tournament_id = tournament_id
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_label(fmt: str) -> str:
    return {
        "singles_se": "Singles · Single Elim",
        "singles_de": "Singles · Double Elim",
        "doubles_se": "Doubles · Single Elim",
        "doubles_de": "Doubles · Double Elim",
    }.get(fmt, fmt)


def _pill(status: str) -> str:
    cls   = {"active": "pill-active", "setup": "pill-setup", "completed": "pill-completed"}.get(status, "")
    label = {"active": "Active", "setup": "Setting up", "completed": "Completed"}.get(status, status)
    return f'<span class="pill {cls}">{label}</span>'


def _top_nav(title: str = "", back: bool = False, back_label: str = "← Home"):
    """Render a minimal top navigation bar."""
    col_l, col_r = st.columns([5, 2])
    with col_l:
        if back:
            if st.button(back_label, key="_back"):
                _go("home")
        else:
            st.markdown(f"### 🎱 Pool Brackets")
    with col_r:
        nav_col1, nav_col2 = st.columns(2)
        with nav_col1:
            if st.button("Stats", key="_nav_stats"):
                _go("stats")
        with nav_col2:
            if st.button("Import", key="_nav_import"):
                _go("import")
    if title:
        st.markdown(f"## {title}")


# ──────────────────────────────────────────────────────────────────────────────
# View: Home
# ──────────────────────────────────────────────────────────────────────────────

def view_home():
    _top_nav()
    st.divider()

    tournaments = get_all_tournaments(sb)
    active = [t for t in tournaments if t["status"] == "active"]
    setup  = [t for t in tournaments if t["status"] == "setup"]
    done   = [t for t in tournaments if t["status"] == "completed"]

    # ── Active tournament banner ─────────────────────────────────────────────
    if active:
        t = active[0]
        n = len(get_competitors(sb, t["id"]))
        st.markdown(
            f'<div class="t-card t-card-active">'
            f'<div class="t-card-title">🎱 {t["name"]}</div>'
            f'<div class="t-card-meta">{_fmt_label(t["format"])} · {n} players · '
            f'{_pill("active")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button("Resume Tournament →", type="primary", key="resume_active"):
            _go("tournament", t["id"])
        st.divider()

    # ── New tournament ───────────────────────────────────────────────────────
    with st.expander("➕ New Tournament", expanded=not bool(active)):
        _new_tournament_form()

    # ── In-setup tournaments ─────────────────────────────────────────────────
    if setup:
        st.markdown('<div class="sh">In Setup</div>', unsafe_allow_html=True)
        for t in setup:
            col1, col2 = st.columns([4, 1])
            with col1:
                n = len(get_competitors(sb, t["id"]))
                st.markdown(
                    f'<div class="t-card-title">{t["name"]}</div>'
                    f'<div class="t-card-meta">{_fmt_label(t["format"])} · {n} players</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                if st.button("Open", key=f"open_setup_{t['id']}"):
                    _go("tournament", t["id"])

    # ── Past tournaments ─────────────────────────────────────────────────────
    if done:
        st.markdown('<div class="sh">Past Tournaments</div>', unsafe_allow_html=True)
        for t in done[:8]:
            col1, col2 = st.columns([4, 1])
            with col1:
                champ = _get_champion(t["id"])
                champ_str = f" · 🏆 {champ}" if champ else ""
                date_str = (t.get("completed_at") or t.get("created_at") or "")[:10]
                st.markdown(
                    f'<div class="t-card-title">{t["name"]}</div>'
                    f'<div class="t-card-meta">{_fmt_label(t["format"])}{champ_str} · {date_str}</div>',
                    unsafe_allow_html=True,
                )
            with col2:
                if st.button("View", key=f"open_done_{t['id']}"):
                    _go("tournament", t["id"])


def _new_tournament_form():
    with st.form("new_t_form", clear_on_submit=True):
        name = st.text_input("Tournament name", placeholder="e.g. Wednesday Night Singles")

        col_mt, col_bt = st.columns(2)
        with col_mt:
            match_type = st.radio(
                "Match type",
                ["Singles (1v1)", "Doubles (2v2)"],
            )
        with col_bt:
            bracket_type = st.radio(
                "Bracket",
                ["Double Elimination", "Single Elimination"],
                help="Double Elim: lose once → second-chance bracket. Single Elim: lose once → out.",
            )

        gf_reset = st.toggle(
            "Grand Final reset",
            value=True,
            help="Double Elim only: if Losers Bracket champion wins the Grand Final, play a 2nd deciding match.",
        )

        if st.form_submit_button("Create & Set Up Roster →", type="primary"):
            if not name.strip():
                st.error("Enter a name.")
            else:
                is_doubles = "Doubles" in match_type
                is_de      = "Double" in bracket_type
                fmt        = ("doubles" if is_doubles else "singles") + ("_de" if is_de else "_se")
                t = create_tournament(sb, name.strip(), fmt, gf_reset and is_de)
                _go("tournament", t["id"])


def _get_champion(tournament_id: str) -> str | None:
    comps = get_competitors(sb, tournament_id)
    champ = next((c for c in comps if c["bracket_status"] == "champion"), None)
    return champ["display_name"] if champ else None


# ──────────────────────────────────────────────────────────────────────────────
# View: Tournament (setup → active → completed)
# ──────────────────────────────────────────────────────────────────────────────

def view_tournament():
    tournament_id = st.session_state.tournament_id
    if not tournament_id:
        _go("home")
        return

    t = get_tournament(sb, tournament_id)
    if not t:
        st.warning("Tournament not found.")
        _go("home")
        return

    _top_nav(back=True, back_label="← Home")
    st.markdown(
        f"## {t['name']} &nbsp; {_pill(t['status'])}",
        unsafe_allow_html=True,
    )
    st.caption(_fmt_label(t["format"]))

    if t["status"] == "setup":
        _tournament_setup(t)
    elif t["status"] == "active":
        _tournament_active(t)
    else:
        _tournament_completed(t)


# ── Setup ─────────────────────────────────────────────────────────────────────

def _tournament_setup(t: dict):
    tournament_id = t["id"]
    fmt = t["format"]
    is_doubles = fmt.startswith("doubles")
    competitors = get_competitors(sb, tournament_id)
    all_player_names = [p["name"] for p in get_all_players(sb)]

    # ── Add player / team ───────────────────────────────────────────────────
    st.markdown('<div class="sh">Add Players</div>', unsafe_allow_html=True)

    if is_doubles:
        _add_doubles_form(tournament_id, all_player_names)
    else:
        _add_singles_form(tournament_id, all_player_names, competitors)

    # ── Roster ───────────────────────────────────────────────────────────────
    if competitors:
        st.markdown(
            f'<div class="sh">Roster &nbsp;·&nbsp; {len(competitors)} entered</div>',
            unsafe_allow_html=True,
        )
        _render_roster(competitors, tournament_id, allow_remove=True)

        st.divider()
        n = len(competitors)
        ready = n >= 2 and (not is_doubles or n % 2 == 0)

        if not ready:
            if n < 2:
                st.caption("Add at least 2 players to start.")
            elif is_doubles and n % 2 != 0:
                st.caption("Doubles requires an even number of teams.")
        else:
            gf_note = ""
            if t["format"].endswith("_de"):
                gf_note = " · GF reset " + ("ON" if t["gf_reset_enabled"] else "OFF")
            st.caption(f"{n} entrant{'s' if n != 1 else ''}{gf_note} · seeding is random")

            if st.button("Start Tournament →", type="primary"):
                with st.spinner("Generating bracket…"):
                    try:
                        start_tournament(sb, tournament_id)
                    except Exception as e:
                        st.error(str(e))
                        st.stop()
                st.rerun()
    else:
        st.info("Add players above to get started.")


def _add_singles_form(tournament_id: str, all_names: list[str], competitors: list[dict]):
    added_ids = {c["player_id"] for c in competitors}

    with st.form("add_player", clear_on_submit=True):
        col1, col2 = st.columns([3, 1])
        with col1:
            raw = st.text_input("Name", placeholder="First L  (e.g. Alex G)", label_visibility="collapsed")
        with col2:
            paid = st.checkbox("Paid")
        submitted = st.form_submit_button("Add", use_container_width=True)

        if submitted and raw.strip():
            name = normalize_name(raw)
            valid, err = validate_name_format(name)
            if not valid:
                st.error(err)
            else:
                player = get_or_create_player(sb, name)
                if player["id"] in added_ids:
                    st.warning(f"{name} is already in this tournament.")
                else:
                    add_competitor(sb, tournament_id, player["id"], display_name=name, paid=paid)
                    st.rerun()

    # Fuzzy suggestions shown outside form
    if "add_player-Name" in st.session_state:
        raw = st.session_state.get("add_player-Name", "")
        if raw:
            similar = find_similar_names(normalize_name(raw), all_names)
            if similar:
                st.caption("Known players: " + "  ·  ".join(similar))


def _add_doubles_form(tournament_id: str, all_names: list[str]):
    with st.form("add_team", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            n1 = st.text_input("Player 1", placeholder="First L")
        with col2:
            n2 = st.text_input("Player 2", placeholder="First L")
        paid = st.checkbox("Team paid")
        submitted = st.form_submit_button("Add Team", use_container_width=True)

        if submitted:
            name1, name2 = normalize_name(n1), normalize_name(n2)
            ok1, e1 = validate_name_format(name1)
            ok2, e2 = validate_name_format(name2)
            if not ok1:
                st.error(f"Player 1: {e1}")
            elif not ok2:
                st.error(f"Player 2: {e2}")
            elif name1 == name2:
                st.error("Players must be different.")
            else:
                p1 = get_or_create_player(sb, name1)
                p2 = get_or_create_player(sb, name2)
                if check_doubles_eligibility(sb, p1["id"]) and check_doubles_eligibility(sb, p2["id"]):
                    st.warning(
                        f"⚠️ Ineligible: both {name1} and {name2} have won a singles "
                        f"tournament in the last 12 months and cannot partner each other."
                    )
                else:
                    display = f"{name1} / {name2}"
                    add_competitor(sb, tournament_id, p1["id"], partner_id=p2["id"],
                                   display_name=display, paid=paid)
                    st.rerun()


def _render_roster(competitors: list[dict], tournament_id: str, allow_remove: bool = False):
    for c in competitors:
        col1, col2, col3 = st.columns([4, 1.5, 0.8])
        with col1:
            st.markdown(f"**{c['display_name']}**")
        with col2:
            label = "✅ Paid" if c["paid"] else "❌ Unpaid"
            if st.button(label, key=f"paid_{c['id']}", use_container_width=True):
                toggle_paid(sb, c["id"], not c["paid"])
                st.rerun()
        with col3:
            if allow_remove and st.button("✕", key=f"rm_{c['id']}", use_container_width=True):
                remove_competitor(sb, c["id"])
                st.rerun()


# ── Active ────────────────────────────────────────────────────────────────────

def _match_label(m: dict) -> str:
    b = {"winners": "WB", "losers": "LB", "grand_final": "Grand Final"}.get(m["bracket"], "")
    r = "Reset" if m["gf_is_reset"] else f"R{m['round_number']}"
    return f"{b} {r}"


def _tournament_active(t: dict):
    tournament_id = t["id"]
    data = get_bracket_data(sb, tournament_id)
    matches     = data["matches"]
    competitors = data["competitors"]
    ready       = data["ready_matches"]
    ready_ids   = {m["id"] for m in ready}

    # ── Interactive bracket (primary action surface) ──────────────────────
    sections = organize_bracket_for_render(matches)
    _render_bracket_native(sections, competitors, ready_ids)

    # ── Recent results (corrections) ─────────────────────────────────────
    completed = [
        m for m in sorted(matches, key=lambda x: x["match_number"], reverse=True)
        if m["status"] == "completed" and not m["is_bye"]
    ][:5]

    if completed:
        with st.expander("✏️ Correct a result", expanded=False):
            editing = st.session_state.editing_match
            for m in completed:
                c1  = competitors.get(m["competitor1_id"] or "")
                c2  = competitors.get(m["competitor2_id"] or "")
                w   = competitors.get(m["winner_id"] or "")
                n1  = c1["display_name"] if c1 else "?"
                n2  = c2["display_name"] if c2 else "?"
                wn  = w["display_name"] if w else "?"

                if editing == m["id"]:
                    st.markdown(
                        f'<div class="match-entry">'
                        f'<div class="match-entry-label">{_match_label(m)}  ·  changing result</div>'
                        f'<strong>{n1}</strong> vs <strong>{n2}</strong>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    btn1, btn2, btn3 = st.columns([2, 2, 1])
                    with btn1:
                        if c1 and st.button(f"🏆 {n1}", key=f"chg_{m['id']}_1", use_container_width=True):
                            try:
                                change_result(sb, m["id"], m["competitor1_id"])
                            except ValueError as e:
                                st.error(str(e))
                            st.session_state.editing_match = None
                            st.rerun()
                    with btn2:
                        if c2 and st.button(f"🏆 {n2}", key=f"chg_{m['id']}_2", use_container_width=True):
                            try:
                                change_result(sb, m["id"], m["competitor2_id"])
                            except ValueError as e:
                                st.error(str(e))
                            st.session_state.editing_match = None
                            st.rerun()
                    with btn3:
                        if st.button("✕", key=f"chg_cancel_{m['id']}", use_container_width=True):
                            st.session_state.editing_match = None
                            st.rerun()
                else:
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.markdown(
                            f'<span style="color:#6B7280;font-size:12px">{_match_label(m)}</span>  '
                            f'{n1} vs {n2}  →  **{wn}**'
                        )
                    with col2:
                        if st.button("✏️", key=f"edit_{m['id']}", use_container_width=True, help="Change result"):
                            st.session_state.editing_match = m["id"]
                            st.rerun()

    # ── Payment status ────────────────────────────────────────────────────
    with st.expander("💰 Payment Status", expanded=False):
        _render_roster(list(competitors.values()), tournament_id, allow_remove=False)


def _render_bracket_native(sections: dict, competitors: dict, ready_ids: set):
    """
    Render the bracket using native Streamlit columns.
    Ready matches are tappable — clicking expands them inline to select a winner.
    """
    selected_id = st.session_state.get("selected_match_id")

    if not ready_ids:
        st.info("No matches ready right now.")

    section_configs = [
        ("winners", "Winners Bracket",
         lambda r, tot: "Final" if r == tot - 1 else "Semis" if r == tot - 2 else f"R{r + 1}"),
        ("losers",  "Losers Bracket",
         lambda r, tot: f"LB R{r + 1}"),
        ("grand_final", "Grand Final",
         lambda r, tot: "GF" if r == 0 else "GF Reset"),
    ]

    for sec_key, sec_label, lbl_fn in section_configs:
        rounds = sections.get(sec_key, [])
        if not rounds or not any(rounds):
            continue

        st.markdown(f'<div class="sh">{sec_label}</div>', unsafe_allow_html=True)

        n_rounds = len(rounds)
        cols = st.columns(n_rounds)

        for r_idx, (col, round_matches) in enumerate(zip(cols, rounds)):
            with col:
                st.caption(lbl_fn(r_idx, n_rounds))
                for match in round_matches:
                    if match.get("is_bye"):
                        continue
                    _render_match_card_native(match, competitors, ready_ids, selected_id)


def _render_match_card_native(
    match: dict,
    competitors: dict,
    ready_ids: set,
    selected_id: str | None,
):
    """Render one match card. Ready matches are interactive."""
    mid   = match["id"]
    c1_id = match.get("competitor1_id")
    c2_id = match.get("competitor2_id")
    c1    = competitors.get(c1_id or "")
    c2    = competitors.get(c2_id or "")
    n1    = c1["display_name"] if c1 else "TBD"
    n2    = c2["display_name"] if c2 else "TBD"
    w_id  = match.get("winner_id")
    status    = match.get("status", "pending")
    is_ready  = mid in ready_ids
    is_sel    = mid == selected_id

    if is_ready and is_sel:
        # Expanded: show winner buttons inline
        st.markdown(
            f'<div style="border:1.5px solid #F59E0B;border-radius:6px;padding:8px 10px;'
            f'margin-bottom:4px;background:#1F2937;">'
            f'<div style="font-size:10px;color:#F59E0B;font-weight:700;">'
            f'{_match_label(match)} · who won?</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if c1 and st.button(f"🏆 {n1}", key=f"win_{mid}_1", use_container_width=True):
            record_result(sb, mid, c1_id)
            st.session_state.selected_match_id = None
            st.rerun()
        if c2 and st.button(f"🏆 {n2}", key=f"win_{mid}_2", use_container_width=True):
            record_result(sb, mid, c2_id)
            st.session_state.selected_match_id = None
            st.rerun()
        if st.button("✕", key=f"cancel_{mid}", use_container_width=True):
            st.session_state.selected_match_id = None
            st.rerun()

    elif is_ready:
        # Ready but not yet tapped — highlighted, tappable
        st.markdown(
            f'<div style="border:1.5px solid #F59E0B;box-shadow:0 0 0 2px rgba(245,158,11,.15);'
            f'border-radius:6px;padding:6px 10px;margin-bottom:2px;background:#1F2937;">'
            f'<div style="font-size:10px;color:#F59E0B;font-weight:700;margin-bottom:3px;">'
            f'{_match_label(match)}</div>'
            f'<div style="color:#F9FAFB;font-size:13px;">{n1}</div>'
            f'<div style="color:#F9FAFB;font-size:13px;border-top:1px solid #374151;'
            f'margin-top:3px;padding-top:3px;">{n2}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button("Tap to enter result", key=f"sel_{mid}", use_container_width=True):
            st.session_state.selected_match_id = mid
            st.rerun()

    elif status == "completed":
        p1_color  = "#4ADE80" if c1_id == w_id else "#6B7280"
        p2_color  = "#4ADE80" if c2_id == w_id else "#6B7280"
        p1_weight = "700" if c1_id == w_id else "400"
        p2_weight = "700" if c2_id == w_id else "400"
        st.markdown(
            f'<div style="border:1px solid #374151;border-radius:6px;padding:6px 10px;'
            f'margin-bottom:6px;background:#1F2937;opacity:0.75;">'
            f'<div style="color:{p1_color};font-weight:{p1_weight};font-size:13px;">{n1}</div>'
            f'<div style="color:{p2_color};font-weight:{p2_weight};font-size:13px;'
            f'border-top:1px solid #2D3748;margin-top:3px;padding-top:3px;">{n2}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    else:
        # Pending — both slots TBD
        st.markdown(
            f'<div style="border:1px solid #1F2937;border-radius:6px;padding:6px 10px;'
            f'margin-bottom:6px;background:#111827;">'
            f'<div style="color:#374151;font-size:13px;">TBD</div>'
            f'<div style="color:#374151;font-size:13px;border-top:1px solid #1D2533;'
            f'margin-top:3px;padding-top:3px;">TBD</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ── Completed ─────────────────────────────────────────────────────────────────

def _tournament_completed(t: dict):
    tournament_id = t["id"]
    data        = get_bracket_data(sb, tournament_id)
    matches     = data["matches"]
    competitors = data["competitors"]

    champ = next((c for c in competitors.values() if c["bracket_status"] == "champion"), None)
    if champ:
        st.success(f"🏆 Champion: **{champ['display_name']}**")

    st.markdown('<div class="sh">Final Bracket</div>', unsafe_allow_html=True)
    sections = organize_bracket_for_render(matches)
    display_comps = {cid: {"display_name": c["display_name"], "paid": c["paid"]}
                     for cid, c in competitors.items()}
    st.components.v1.html(
        render_bracket_html(sections, display_comps),
        height=_bracket_height(matches), scrolling=True,
    )

    with st.expander("Results"):
        for m in sorted(matches, key=lambda x: x["match_number"]):
            if m["status"] != "completed" or m["is_bye"]:
                continue
            c1 = competitors.get(m["competitor1_id"] or "")
            c2 = competitors.get(m["competitor2_id"] or "")
            w  = competitors.get(m["winner_id"] or "")
            b  = {"winners": "WB", "losers": "LB", "grand_final": "GF"}.get(m["bracket"], "")
            st.markdown(
                f"**{b} R{m['round_number']}**: "
                f"{c1['display_name'] if c1 else '?'} vs "
                f"{c2['display_name'] if c2 else '?'} → "
                f"**{w['display_name'] if w else '?'}**"
            )


def _bracket_height(matches: list[dict]) -> int:
    from collections import Counter
    r1_wb = Counter(m["round_number"] for m in matches if m["bracket"] == "winners").get(1, 1)
    r1_lb = Counter(m["round_number"] for m in matches if m["bracket"] == "losers").get(1, 0)
    return max(r1_wb * 76 + 80 + r1_lb * 76 + 80 + 120, 300)


# ──────────────────────────────────────────────────────────────────────────────
# View: Stats
# ──────────────────────────────────────────────────────────────────────────────

def view_stats():
    _top_nav(back=True, back_label="← Home")
    st.markdown("## Stats")

    players = get_all_players(sb)
    if not players:
        st.info("No players yet.")
        return

    # Leaderboard
    st.markdown('<div class="sh">Leaderboard</div>', unsafe_allow_html=True)
    board = get_leaderboard(sb)
    for i, row in enumerate(board[:10]):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
        col1, col2 = st.columns([4, 2])
        with col1:
            st.markdown(f"{medal} **{row['name']}**")
        with col2:
            st.caption(f"{row['won']}W / {row['played'] - row['won']}L ({row['win_rate']:.0%})")

    st.divider()

    # Player deep-dive
    st.markdown('<div class="sh">Player Profile</div>', unsafe_allow_html=True)
    options = {p["id"]: p["name"] for p in players}
    sel_id = st.selectbox("Player", list(options.keys()), format_func=lambda x: options[x])

    if not sel_id:
        return

    with st.spinner("Loading..."):
        stats = get_player_stats(sb, sel_id)

    if stats["tournaments_played"] == 0:
        st.info("No tournament history yet.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Played",     stats["tournaments_played"])
    c2.metric("Won",        stats["tournaments_won"])
    c3.metric("Match W/L",  f"{stats['match_wins']}/{stats['match_losses']}")
    c4.metric("Match Win%", f"{stats['match_win_rate']:.0%}")

    col_l, col_r = st.columns(2)
    with col_l:
        if stats["nemesis"]:
            n = stats["nemesis"]
            st.markdown(f"😬 **Nemesis**: {n['name']} *(lost {n['losses']}×)*")
        if stats["best_victim"]:
            v = stats["best_victim"]
            st.markdown(f"😈 **Best victim**: {v['name']} *(won {v['wins']}×)*")
    with col_r:
        if stats["typical_stage"] != "N/A":
            st.markdown(f"📍 **Usually out at**: {stats['typical_stage']}")
        if stats["recent_form"]:
            form = "".join(
                f'<span class="{"fw" if r == "W" else "fl"}">{r}</span>'
                for r in stats["recent_form"]
            )
            st.markdown(f"**Recent form**: {form}", unsafe_allow_html=True)

    if stats["stage_breakdown"]:
        import plotly.express as px
        import pandas as pd
        df = pd.DataFrame(list(stats["stage_breakdown"].items()), columns=["Stage", "Times"])
        df = df.sort_values("Times", ascending=True)
        fig = px.bar(df, x="Times", y="Stage", orientation="h",
                     color_discrete_sequence=["#F59E0B"])
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#D1D5DB", margin=dict(l=0,r=0,t=8,b=0),
            yaxis_title=None, xaxis_title=None,
            height=max(150, len(df) * 40),
        )
        fig.update_xaxes(gridcolor="#374151", tickfont_color="#9CA3AF")
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickfont_color="#9CA3AF")
        st.plotly_chart(fig, use_container_width=True)


# ──────────────────────────────────────────────────────────────────────────────
# View: Import
# ──────────────────────────────────────────────────────────────────────────────

def view_import():
    _top_nav(back=True, back_label="← Home")
    st.markdown("## Import Historical Data")

    st.markdown("""
Upload a CSV of past tournament results. Each row = one player's result in one tournament.

| Column | Example |
|---|---|
| `date` | `2024-03-15` |
| `tournament_name` | `March Singles` |
| `format` | `singles_se`, `singles_de`, `doubles_se`, or `doubles_de` |
| `player_name` | `Alex G` |
| `placement` | `1` (1 = winner) |
| `paid` | `true` or `false` |
""")

    template = [
        ["date","tournament_name","format","player_name","placement","paid"],
        ["2024-01-15","January Night","singles_se","Alex G","1","true"],
        ["2024-01-15","January Night","singles_se","Sam T","2","false"],
    ]
    buf = io.StringIO()
    csv.writer(buf).writerows(template)
    st.download_button("⬇️ Download Template", buf.getvalue(),
                       "import_template.csv", "text/csv")

    uploaded = st.file_uploader("Upload CSV", type="csv")
    if not uploaded:
        return

    try:
        records = list(csv.DictReader(io.StringIO(uploaded.read().decode("utf-8"))))
    except Exception as e:
        st.error(f"Could not parse: {e}")
        return

    st.write(f"{len(records)} rows found. Preview:")
    st.dataframe(records[:10], use_container_width=True)

    if st.button("Import", type="primary"):
        with st.spinner("Importing..."):
            result = import_historical(sb, records)
        st.success(f"Imported {result['imported']} records.")
        if result["errors"]:
            for err in result["errors"][:10]:
                st.caption(f"• {err}")


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────

view = st.session_state.view
if view == "home":
    view_home()
elif view == "tournament":
    view_tournament()
elif view == "stats":
    view_stats()
elif view == "import":
    view_import()
