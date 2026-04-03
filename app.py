"""
Pool Brackets — Tournament bracket manager for pool/billiards.
Supports single elimination, double elimination, and doubles (2v2).
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
    import_historical,
)
from utils.bracket import render_bracket_html
from utils.names import normalize_name, validate_name_format, find_similar_names
from utils.stats import get_player_stats, get_leaderboard, get_head_to_head

st.set_page_config(
    page_title="Pool Brackets",
    page_icon="🎱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────────────────────
# Global CSS
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Mobile-friendly base */
[data-testid="stAppViewContainer"] { max-width: 100%; }
div[data-testid="column"] { min-width: 0 !important; }

/* Bigger tap targets */
.stButton > button {
    min-height: 44px;
    font-size: 15px;
    border-radius: 8px;
}
.stButton > button:hover { border-color: #F59E0B; }

/* Compact metric cards */
[data-testid="stMetric"] {
    background: #1F2937;
    border-radius: 8px;
    padding: 12px 16px !important;
    border: 1px solid #374151;
}

/* Section divider */
.section-header {
    font-size: 13px;
    font-weight: 700;
    color: #9CA3AF;
    text-transform: uppercase;
    letter-spacing: .06em;
    margin: 20px 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid #374151;
}

/* Status pill */
.pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
}
.pill-active   { background: rgba(245,158,11,.15); color: #F59E0B; }
.pill-setup    { background: rgba(99,102,241,.15);  color: #818CF8; }
.pill-completed{ background: rgba(34,197,94,.15);   color: #4ADE80; }

/* Paid badge */
.paid-badge   { color: #4ADE80; font-weight: 700; }
.unpaid-badge { color: #EF4444; font-weight: 700; }

/* Form display */
.form-char { display: inline-block; width: 20px; text-align: center; font-weight: 700; }
.form-w { color: #4ADE80; }
.form-l { color: #EF4444; }

/* Match card (for result entry) */
.match-card {
    background: #1F2937;
    border: 1.5px solid #F59E0B;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase connection
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_sb() -> Client:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        st.error("Missing SUPABASE_URL / SUPABASE_KEY environment variables.")
        st.stop()
    return get_client(url, key)


sb = _get_sb()


# ──────────────────────────────────────────────────────────────────────────────
# Session state helpers
# ──────────────────────────────────────────────────────────────────────────────

def _init_state():
    defaults = {
        "selected_tournament_id": None,
        "page": "Tournaments",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar navigation
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🎱 Pool Brackets")
    st.divider()
    page = st.radio(
        "Navigate",
        ["🏆 Tournaments", "🎱 Bracket", "📊 Stats", "📥 Import"],
        label_visibility="collapsed",
        key="nav",
    )
    st.divider()

    # Tournament quick-selector (shown on bracket page)
    if "Bracket" in page:
        tournaments = get_all_tournaments(sb)
        if tournaments:
            options = {t["id"]: t["name"] for t in tournaments}
            sel_id = st.selectbox(
                "Tournament",
                list(options.keys()),
                format_func=lambda x: options[x],
                index=(
                    list(options.keys()).index(st.session_state.selected_tournament_id)
                    if st.session_state.selected_tournament_id in options
                    else 0
                ),
                key="tournament_picker",
            )
            st.session_state.selected_tournament_id = sel_id


# ──────────────────────────────────────────────────────────────────────────────
# Page: Tournaments
# ──────────────────────────────────────────────────────────────────────────────

def format_label(fmt: str) -> str:
    return {
        "singles_se": "Singles · Single Elim",
        "singles_de": "Singles · Double Elim",
        "doubles_se": "Doubles · Single Elim",
        "doubles_de": "Doubles · Double Elim",
    }.get(fmt, fmt)


def status_pill(status: str) -> str:
    cls = {"active": "pill-active", "setup": "pill-setup", "completed": "pill-completed"}.get(status, "")
    label = {"active": "Active", "setup": "Setup", "completed": "Complete"}.get(status, status)
    return f'<span class="pill {cls}">{label}</span>'


def page_tournaments():
    st.title("Tournaments")

    # ── Create new tournament ────────────────────────────────────────────────
    with st.expander("➕ Create New Tournament", expanded=False):
        with st.form("create_tournament_form"):
            t_name = st.text_input("Tournament name", placeholder="e.g. April Singles Night")
            col_mt, col_bt = st.columns(2)
            with col_mt:
                match_type = st.radio(
                    "Match type",
                    ["Singles (1v1)", "Doubles (2v2)"],
                    help="Singles = individual players. Doubles = teams of 2.",
                )
            with col_bt:
                bracket_type = st.radio(
                    "Bracket format",
                    ["Double Elimination", "Single Elimination"],
                    help="Double Elim: one loss drops you to a second-chance bracket. Single Elim: one loss and you're out.",
                )
            gf_reset = st.toggle(
                "Grand Final reset match",
                value=True,
                help="Double Elim only: if the Losers Bracket champion wins the Grand Final, play a 2nd deciding match.",
            )
            submitted = st.form_submit_button("Create Tournament", type="primary")
            if submitted:
                if not t_name.strip():
                    st.error("Enter a tournament name.")
                else:
                    is_doubles = "Doubles" in match_type
                    is_de = "Double" in bracket_type
                    fmt = ("doubles" if is_doubles else "singles") + ("_de" if is_de else "_se")
                    use_reset = gf_reset and is_de
                    t = create_tournament(sb, t_name.strip(), fmt, use_reset)
                    st.session_state.selected_tournament_id = t["id"]
                    st.session_state.nav = "🎱 Bracket"
                    st.success(f"Created **{t_name}**.")
                    st.rerun()

    st.divider()

    # ── Tournament list ──────────────────────────────────────────────────────
    tournaments = get_all_tournaments(sb)
    if not tournaments:
        st.info("No tournaments yet. Create one above.")
        return

    for t in tournaments:
        col1, col2, col3 = st.columns([3, 1.5, 1.5])
        with col1:
            st.markdown(f"**{t['name']}**")
            n_players = len(get_competitors(sb, t["id"]))
            st.caption(f"{format_label(t['format'])} · {n_players} player{'s' if n_players != 1 else ''}")
        with col2:
            st.markdown(status_pill(t["status"]), unsafe_allow_html=True)
        with col3:
            if st.button("Open →", key=f"open_{t['id']}"):
                st.session_state.selected_tournament_id = t["id"]
                st.session_state.nav = "🎱 Bracket"
                st.rerun()
        st.divider()


# ──────────────────────────────────────────────────────────────────────────────
# Page: Bracket (setup + active)
# ──────────────────────────────────────────────────────────────────────────────

def page_bracket():
    tournament_id = st.session_state.get("selected_tournament_id")
    if not tournament_id:
        st.info("Select a tournament from the sidebar, or create one on the Tournaments page.")
        return

    tournament = get_tournament(sb, tournament_id)
    if not tournament:
        st.warning("Tournament not found.")
        return

    status = tournament["status"]
    fmt = tournament["format"]

    # Header
    col_title, col_pill = st.columns([4, 1])
    with col_title:
        st.title(tournament["name"])
    with col_pill:
        st.markdown(
            f"<div style='padding-top:18px'>{status_pill(status)}</div>",
            unsafe_allow_html=True,
        )
    st.caption(format_label(fmt))

    if status == "setup":
        _bracket_setup(tournament)
    elif status == "active":
        _bracket_active(tournament)
    else:
        _bracket_completed(tournament)


def _bracket_setup(tournament: dict):
    """Roster management during setup phase."""
    tournament_id = tournament["id"]
    fmt = tournament["format"]
    competitors = get_competitors(sb, tournament_id)
    all_players = get_all_players(sb)
    existing_names = [p["name"] for p in all_players]

    st.markdown('<div class="section-header">Roster</div>', unsafe_allow_html=True)

    # ── Add player / team ───────────────────────────────────────────────────
    with st.expander("➕ Add Player" + (" / Team" if fmt == "doubles" else ""), expanded=len(competitors) == 0):
        if fmt == "doubles":
            _add_doubles_team(tournament_id, existing_names)
        else:
            _add_singles_player(tournament_id, existing_names, competitors)

    # ── Current roster ──────────────────────────────────────────────────────
    if competitors:
        _render_roster(competitors, tournament_id, allow_remove=True)

        st.divider()

        # Start tournament button
        n = len(competitors)
        if n < 2:
            st.warning("Add at least 2 players to start.")
        else:
            if fmt == "doubles" and n % 2 != 0:
                st.warning("Doubles requires an even number of teams.")
            else:
                col1, col2 = st.columns([2, 1])
                with col1:
                    gf_note = ""
                    if tournament["format"] != "single_elim":
                        gf_note = " · GF reset " + ("ON" if tournament["gf_reset_enabled"] else "OFF")
                    st.caption(f"{n} entrant{'s' if n != 1 else ''}{gf_note}")
                with col2:
                    if st.button("🎱 Start Tournament", type="primary", use_container_width=True):
                        try:
                            start_tournament(sb, tournament_id)
                            st.success("Tournament started! Bracket generated.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))
    else:
        st.info("Add players to get started.")


def _add_singles_player(tournament_id: str, existing_names: list[str], competitors: list[dict]):
    """Form to add a single player to the roster."""
    # Already-added player ids to exclude from suggestions
    added_player_ids = {c["player_id"] for c in competitors}

    with st.form("add_player_form", clear_on_submit=True):
        raw_name = st.text_input("Player name", placeholder="First L (e.g. Alex G)")
        paid = st.checkbox("Mark as paid")
        submitted = st.form_submit_button("Add Player")

        if submitted:
            normalized = normalize_name(raw_name)
            valid, err = validate_name_format(normalized)
            if not valid:
                st.error(err)
            else:
                # Check for similar existing names
                similar = find_similar_names(normalized, existing_names)
                # Use the normalized name directly — user confirmed it
                player = get_or_create_player(sb, normalized)
                if player["id"] in added_player_ids:
                    st.warning(f"**{normalized}** is already in this tournament.")
                else:
                    add_competitor(sb, tournament_id, player["id"], display_name=normalized, paid=paid)
                    st.success(f"Added **{normalized}**")
                    st.rerun()

    # Name suggestions (outside form, for guidance)
    raw = st.session_state.get("add_player_form-player_name", "")
    if raw:
        normalized = normalize_name(raw)
        similar = find_similar_names(normalized, existing_names)
        if similar:
            st.caption("Similar existing names: " + ", ".join(similar))


def _add_doubles_team(tournament_id: str, existing_names: list[str]):
    """Form to add a doubles team (two players)."""
    with st.form("add_team_form", clear_on_submit=True):
        st.caption("Enter both players. A warning will appear if both have won a singles tournament in the last 12 months.")
        col1, col2 = st.columns(2)
        with col1:
            name1 = st.text_input("Player 1", placeholder="First L")
        with col2:
            name2 = st.text_input("Player 2", placeholder="First L")
        paid = st.checkbox("Mark team as paid")
        submitted = st.form_submit_button("Add Team")

        if submitted:
            n1 = normalize_name(name1)
            n2 = normalize_name(name2)
            ok1, e1 = validate_name_format(n1)
            ok2, e2 = validate_name_format(n2)
            if not ok1:
                st.error(f"Player 1: {e1}")
            elif not ok2:
                st.error(f"Player 2: {e2}")
            elif n1 == n2:
                st.error("Players must be different.")
            else:
                p1 = get_or_create_player(sb, n1)
                p2 = get_or_create_player(sb, n2)
                # Eligibility check
                p1_won = check_doubles_eligibility(sb, p1["id"])
                p2_won = check_doubles_eligibility(sb, p2["id"])
                if p1_won and p2_won:
                    st.warning(
                        f"⚠️ **Ineligible pairing**: both {n1} and {n2} have won a "
                        f"singles tournament in the last 12 months. They cannot partner each other."
                    )
                else:
                    display = f"{n1} / {n2}"
                    add_competitor(sb, tournament_id, p1["id"], partner_id=p2["id"],
                                   display_name=display, paid=paid)
                    st.success(f"Added team **{display}**")
                    st.rerun()


def _render_roster(competitors: list[dict], tournament_id: str, allow_remove: bool = False):
    """Display current roster with paid toggle and optional remove."""
    for comp in competitors:
        col1, col2, col3 = st.columns([4, 1.5, 1])
        with col1:
            st.markdown(f"**{comp['display_name']}**")
        with col2:
            paid_label = "✅ Paid" if comp["paid"] else "❌ Unpaid"
            if st.button(paid_label, key=f"paid_toggle_{comp['id']}", use_container_width=True):
                toggle_paid(sb, comp["id"], not comp["paid"])
                st.rerun()
        with col3:
            if allow_remove:
                if st.button("✕", key=f"remove_{comp['id']}", help="Remove from roster"):
                    remove_competitor(sb, comp["id"])
                    st.rerun()


def _bracket_active(tournament: dict):
    """Active tournament: bracket visualization + match result entry."""
    tournament_id = tournament["id"]
    data = get_bracket_data(sb, tournament_id)
    matches = data["matches"]
    competitors = data["competitors"]
    ready_matches = data["ready_matches"]

    # ── Bracket visualization ───────────────────────────────────────────────
    st.markdown('<div class="section-header">Bracket</div>', unsafe_allow_html=True)

    sections = organize_bracket_for_render(matches)
    # Build display competitor map for bracket renderer
    display_comps = {
        cid: {"display_name": c["display_name"], "paid": c["paid"]}
        for cid, c in competitors.items()
    }
    bracket_html = render_bracket_html(sections, display_comps)
    st.components.v1.html(bracket_html, height=_estimate_bracket_height(matches), scrolling=True)

    # ── Roster / paid status ─────────────────────────────────────────────────
    with st.expander("💰 Payment Status", expanded=False):
        comps_list = list(competitors.values())
        _render_roster(comps_list, tournament_id, allow_remove=False)

    # ── Match result entry ──────────────────────────────────────────────────
    st.markdown('<div class="section-header">Ready to Play</div>', unsafe_allow_html=True)

    if not ready_matches:
        st.info("No matches ready right now. Check back after pending matches complete.")
        return

    for match in ready_matches:
        c1 = competitors.get(match["competitor1_id"] or "")
        c2 = competitors.get(match["competitor2_id"] or "")
        n1 = c1["display_name"] if c1 else "TBD"
        n2 = c2["display_name"] if c2 else "TBD"

        bracket_label = {
            "winners": "WB", "losers": "LB", "grand_final": "Grand Final"
        }.get(match["bracket"], match["bracket"])
        round_label = f"R{match['round_number']}"
        if match["gf_is_reset"]:
            round_label = "Reset"

        st.markdown(
            f'<div class="match-card">'
            f'<strong>{bracket_label} {round_label}</strong>: '
            f'{n1} vs {n2}'
            f'</div>',
            unsafe_allow_html=True,
        )

        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            if c1 and st.button(f"🏆 {n1}", key=f"win_{match['id']}_c1", use_container_width=True):
                try:
                    record_result(sb, match["id"], match["competitor1_id"])
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        with col2:
            if c2 and st.button(f"🏆 {n2}", key=f"win_{match['id']}_c2", use_container_width=True):
                try:
                    record_result(sb, match["id"], match["competitor2_id"])
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        with col3:
            st.caption("")  # spacer


def _bracket_completed(tournament: dict):
    """Completed tournament view."""
    tournament_id = tournament["id"]
    data = get_bracket_data(sb, tournament_id)
    matches = data["matches"]
    competitors = data["competitors"]

    # Find champion
    champion = next((c for c in competitors.values() if c["bracket_status"] == "champion"), None)
    if champion:
        st.success(f"🏆 Champion: **{champion['display_name']}**")

    # Bracket display
    st.markdown('<div class="section-header">Final Bracket</div>', unsafe_allow_html=True)
    sections = organize_bracket_for_render(matches)
    display_comps = {
        cid: {"display_name": c["display_name"], "paid": c["paid"]}
        for cid, c in competitors.items()
    }
    bracket_html = render_bracket_html(sections, display_comps)
    st.components.v1.html(bracket_html, height=_estimate_bracket_height(matches), scrolling=True)

    # Results table
    with st.expander("📋 Full Results"):
        for m in sorted(matches, key=lambda x: x["match_number"]):
            if m["status"] != "completed" or m["is_bye"]:
                continue
            c1 = competitors.get(m["competitor1_id"] or "")
            c2 = competitors.get(m["competitor2_id"] or "")
            w = competitors.get(m["winner_id"] or "")
            n1 = c1["display_name"] if c1 else "BYE"
            n2 = c2["display_name"] if c2 else "BYE"
            wn = w["display_name"] if w else "?"
            b = {"winners": "WB", "losers": "LB", "grand_final": "GF"}.get(m["bracket"], "")
            st.markdown(f"**{b} R{m['round_number']}**: {n1} vs {n2} → **{wn}**")


def _estimate_bracket_height(matches: list[dict]) -> int:
    """Estimate height needed for the bracket component iframe."""
    from collections import Counter
    r1_wb = max(
        (Counter(m["round_number"] for m in matches if m["bracket"] == "winners").get(1, 0), 1)
    )
    r1_lb = max(
        (Counter(m["round_number"] for m in matches if m["bracket"] == "losers").get(1, 0), 0)
    )
    h_wb = r1_wb * 76 + 80   # match height + label/section padding
    h_lb = r1_lb * 76 + 80 if r1_lb else 0
    return max(h_wb + h_lb + 200, 300)


# ──────────────────────────────────────────────────────────────────────────────
# Page: Stats
# ──────────────────────────────────────────────────────────────────────────────

def page_stats():
    st.title("Stats")

    players = get_all_players(sb)
    if not players:
        st.info("No players yet. Create a tournament and add players first.")
        return

    # ── Leaderboard ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Leaderboard</div>', unsafe_allow_html=True)
    board = get_leaderboard(sb)
    if board:
        for i, row in enumerate(board[:10]):
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
            col1, col2, col3 = st.columns([1, 4, 2])
            with col1:
                st.markdown(medal)
            with col2:
                st.markdown(f"**{row['name']}**")
            with col3:
                st.caption(
                    f"{row['won']}W / {row['played'] - row['won']}L  "
                    f"({row['win_rate']:.0%})"
                )

    st.divider()

    # ── Player deep-dive ─────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Player Profile</div>', unsafe_allow_html=True)
    player_options = {p["id"]: p["name"] for p in players}
    sel_player_id = st.selectbox(
        "Select player",
        list(player_options.keys()),
        format_func=lambda x: player_options[x],
    )

    if not sel_player_id:
        return

    with st.spinner("Loading stats..."):
        stats = get_player_stats(sb, sel_player_id)

    if stats["tournaments_played"] == 0:
        st.info("No tournament history for this player yet.")
        return

    # Metric cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tournaments", stats["tournaments_played"])
    c2.metric("Wins", stats["tournaments_won"])
    c3.metric(
        "Match W/L",
        f"{stats['match_wins']}/{stats['match_losses']}",
        f"{stats['match_win_rate']:.0%}"
    )
    c4.metric("Win Rate", f"{stats['win_rate']:.0%}")

    # Interesting insights
    st.markdown('<div class="section-header">Insights</div>', unsafe_allow_html=True)

    col_l, col_r = st.columns(2)
    with col_l:
        if stats["nemesis"]:
            n = stats["nemesis"]
            st.markdown(
                f"😬 **Nemesis**: {n['name']} "
                f"<span style='color:#EF4444'>({n['losses']} losses)</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown("😎 No clear nemesis yet.")

        if stats["best_victim"]:
            v = stats["best_victim"]
            st.markdown(
                f"😈 **Best victim**: {v['name']} "
                f"<span style='color:#4ADE80'>({v['wins']} wins)</span>",
                unsafe_allow_html=True,
            )

    with col_r:
        if stats["typical_stage"] != "N/A":
            st.markdown(f"📍 **Usually eliminated at**: {stats['typical_stage']}")

        if stats["recent_form"]:
            form_html = "🎱 **Recent form**: "
            for r in stats["recent_form"]:
                cls = "form-w" if r == "W" else "form-l"
                form_html += f'<span class="form-char {cls}">{r}</span>'
            st.markdown(form_html, unsafe_allow_html=True)

    # Stage breakdown
    if stats["stage_breakdown"]:
        st.markdown('<div class="section-header">Elimination Stages</div>', unsafe_allow_html=True)
        import plotly.express as px
        import pandas as pd
        df = pd.DataFrame(
            list(stats["stage_breakdown"].items()),
            columns=["Stage", "Times"]
        ).sort_values("Times", ascending=True)
        fig = px.bar(
            df, x="Times", y="Stage", orientation="h",
            color_discrete_sequence=["#F59E0B"],
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#D1D5DB",
            margin=dict(l=0, r=0, t=8, b=0),
            yaxis_title=None,
            xaxis_title="Times eliminated here",
            height=max(150, len(df) * 40),
        )
        fig.update_xaxes(color="#6B7280", gridcolor="#374151", tickfont=dict(color="#9CA3AF"))
        fig.update_yaxes(color="#9CA3AF", gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    # Format stats
    st.markdown('<div class="section-header">By Format</div>', unsafe_allow_html=True)
    fc1, fc2, fc3, fc4 = st.columns(4)
    for col, (fmt, label) in zip(
        [fc1, fc2, fc3, fc4],
        [
            ("singles_se", "Singles SE"),
            ("singles_de", "Singles DE"),
            ("doubles_se", "Doubles SE"),
            ("doubles_de", "Doubles DE"),
        ]
    ):
        fs = stats["format_stats"].get(fmt, {"played": 0, "won": 0})
        col.metric(label, f"{fs['won']}W / {fs['played']}P" if fs["played"] else "—")


# ──────────────────────────────────────────────────────────────────────────────
# Page: Import
# ──────────────────────────────────────────────────────────────────────────────

def page_import():
    st.title("Import Historical Data")

    st.markdown("""
Upload a CSV with past tournament results. Each row is one player's result in one tournament.

**Required columns:**
| Column | Description | Example |
|---|---|---|
| `date` | Tournament date | `2024-03-15` |
| `tournament_name` | Name of tournament | `March Singles` |
| `format` | `single_elim`, `double_elim`, or `doubles` | `single_elim` |
| `player_name` | Must match "First L" format | `Alex G` |
| `placement` | Final placement (1 = winner) | `1` |
| `paid` | `true` or `false` | `true` |
""")

    # Template download
    template_rows = [
        ["date", "tournament_name", "format", "player_name", "placement", "paid"],
        ["2024-01-15", "January Night", "single_elim", "Alex G", "1", "true"],
        ["2024-01-15", "January Night", "single_elim", "Sam T", "2", "false"],
        ["2024-01-15", "January Night", "single_elim", "Jordan B", "3", "true"],
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(template_rows)
    st.download_button(
        "⬇️ Download Template CSV",
        data=buf.getvalue(),
        file_name="pool_brackets_import_template.csv",
        mime="text/csv",
    )

    st.divider()

    uploaded = st.file_uploader("Upload CSV", type="csv")
    if uploaded is None:
        return

    # Parse CSV
    try:
        content = uploaded.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        records = list(reader)
    except Exception as e:
        st.error(f"Could not parse CSV: {e}")
        return

    if not records:
        st.warning("CSV is empty.")
        return

    st.write(f"**{len(records)} rows** found. Preview:")
    st.dataframe(records[:10], use_container_width=True)

    if st.button("Import", type="primary"):
        with st.spinner("Importing..."):
            result = import_historical(sb, records)
        st.success(f"Imported {result['imported']} competitor records.")
        if result["errors"]:
            st.warning("Some rows had errors:")
            for err in result["errors"][:10]:
                st.caption(f"• {err}")


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────

if "Tournaments" in page:
    page_tournaments()
elif "Bracket" in page:
    page_bracket()
elif "Stats" in page:
    page_stats()
elif "Import" in page:
    page_import()
