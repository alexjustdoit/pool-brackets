"""
Bracket generation for single elimination and double elimination.

Terminology:
  WB  = Winners Bracket
  LB  = Losers Bracket
  GF  = Grand Final
  k   = log2(size), where size = next power of 2 >= n_competitors

Double elimination structure for 2^k players:
  WB rounds:     1 .. k
  LB rounds:     1 .. 2*(k-1)
    Odd LB rounds  = survival rounds (LB winners play each other)
    Even LB rounds = injection rounds (LB survivors vs WB losers)
  Grand Final:   GF game 1 (+ optional reset if LB champ wins)

Cross-pairing in LB prevents immediate rematches:
  LB-R1: WB-R1 losers are cross-paired (first-quarter loser vs last-quarter loser)
  LB-R2: LB-R1 winners are paired with WB-R2 losers in reversed order
"""

import math
from typing import Literal


BracketType = Literal["winners", "losers", "grand_final"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def next_power_of_2(n: int) -> int:
    if n <= 1:
        return 1
    k = 1
    while k < n:
        k *= 2
    return k


def seeding_order(size: int) -> list[int]:
    """
    Return standard tournament seeding sequence so that the highest-seeded
    opponents meet only in the latest possible round.

    seeding_order(8) → [1, 8, 4, 5, 2, 7, 3, 6]
    Matches: (1v8), (4v5), (2v7), (3v6)
    Top half = seeds 1,4,5,8 · Bottom half = seeds 2,3,6,7
    """
    if size == 1:
        return [1]
    half = seeding_order(size // 2)
    result = []
    for s in half:
        result.append(s)
        result.append(size + 1 - s)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Bracket generator
# ──────────────────────────────────────────────────────────────────────────────

def generate_bracket(n_competitors: int, format: str) -> list[dict]:
    """
    Generate the full bracket match structure for `n_competitors` players.

    format: 'single_elim' | 'double_elim'  (doubles tournaments use the same
            bracket logic — the "competitor" is just a team)

    Returns a list of match dicts, each with:
      match_number          int   — sequential 1..N, used to wire routing
      round_number          int
      bracket               str   — 'winners' | 'losers' | 'grand_final'
      slot1_source          tuple — ('seed', S) | ('winner', M) | ('loser', M)
      slot2_source          tuple
      winner_next_match_number  int | None
      winner_next_slot          int | None  (1 or 2)
      loser_next_match_number   int | None
      loser_next_slot           int | None

    Byes are handled later in start_tournament() by examining which seed slots
    exceed n_competitors and marking them slot_is_bye=True.
    """
    size = next_power_of_2(n_competitors)
    seeds = seeding_order(size)
    k = int(math.log2(size))  # size == 2**k

    matches: dict[int, dict] = {}
    _counter = [1]

    def add(round_num: int, bracket: BracketType, slot1, slot2) -> int:
        mn = _counter[0]
        matches[mn] = {
            "match_number": mn,
            "round_number": round_num,
            "bracket": bracket,
            "slot1_source": slot1,
            "slot2_source": slot2,
            "winner_next_match_number": None,
            "winner_next_slot": None,
            "loser_next_match_number": None,
            "loser_next_slot": None,
        }
        _counter[0] += 1
        return mn

    # ── Winners Bracket ──────────────────────────────────────────────────────
    wb_rounds: dict[int, list[int]] = {}

    # WB Round 1: consecutive pairs from seeding_order
    wb_r1 = []
    for i in range(0, size, 2):
        mn = add(1, "winners", ("seed", seeds[i]), ("seed", seeds[i + 1]))
        wb_r1.append(mn)
    wb_rounds[1] = wb_r1

    # WB Rounds 2 .. k
    prev = wb_r1
    for r in range(2, k + 1):
        curr = []
        for i in range(0, len(prev), 2):
            mn = add(r, "winners", ("winner", prev[i]), ("winner", prev[i + 1]))
            curr.append(mn)
        wb_rounds[r] = curr
        prev = curr

    wb_final = wb_rounds[k][0]

    if format == "single_elim":
        _compute_routing(matches)
        return list(matches.values())

    # ── Losers Bracket ───────────────────────────────────────────────────────
    # LB has 2*(k-1) rounds.
    # LB-R1 (survival): WB-R1 losers cross-paired.
    # LB-R2 (injection, even): LB survivors vs WB-R2 losers (reversed pairing).
    # LB-R3 (survival, odd): LB-R2 winners play each other.
    # … alternates until all WB losers are absorbed.

    lb_rounds: dict[int, list[int]] = {}

    # LB-R1: cross-pair WB-R1 losers
    #   For 4 WB-R1 matches [M1,M2,M3,M4]:
    #   → L(M1) vs L(M4), L(M2) vs L(M3)   (first-half vs second-half mirror)
    r1_losers = wb_rounds[1]
    n_wb_r1 = len(r1_losers)
    lb_r1 = []
    for i in range(n_wb_r1 // 2):
        ma = r1_losers[i]
        mb = r1_losers[n_wb_r1 - 1 - i]
        mn = add(1, "losers", ("loser", ma), ("loser", mb))
        lb_r1.append(mn)
    lb_rounds[1] = lb_r1
    lb_prev = lb_r1

    # LB-R2 .. LB-R(2*(k-1))
    for lb_r in range(2, 2 * (k - 1) + 1):
        if lb_r % 2 == 0:
            # Injection: LB survivors paired (reversed) with WB-Rj losers.
            # j = lb_r // 2 + 1  (which WB round's losers drop in)
            wb_r_num = lb_r // 2 + 1
            wb_inj = wb_rounds[wb_r_num]
            curr = []
            # Reversed pairing avoids rematches with the players who lost to this seed.
            for lb_m, wb_m in zip(lb_prev, reversed(wb_inj)):
                mn = add(lb_r, "losers", ("winner", lb_m), ("loser", wb_m))
                curr.append(mn)
        else:
            # Survival: LB winners play each other
            curr = []
            for i in range(0, len(lb_prev), 2):
                mn = add(lb_r, "losers", ("winner", lb_prev[i]), ("winner", lb_prev[i + 1]))
                curr.append(mn)
        lb_rounds[lb_r] = curr
        lb_prev = curr

    lb_final = lb_rounds[2 * (k - 1)][0]

    # ── Grand Final ──────────────────────────────────────────────────────────
    # slot1 = WB champion (undefeated), slot2 = LB champion (one loss).
    # GF reset is created dynamically by record_result() if LB champ wins GF game 1.
    add(1, "grand_final", ("winner", wb_final), ("winner", lb_final))

    # ── Wire routing (second pass) ────────────────────────────────────────────
    _compute_routing(matches)

    return list(matches.values())


def _compute_routing(matches: dict[int, dict]) -> None:
    """
    Fill in winner_next / loser_next on each match by examining all slot sources.
    A slot source of ('winner', M) means: match M's winner fills this slot.
    A slot source of ('loser', M) means: match M's loser fills this slot.
    """
    for match in matches.values():
        for slot_num, source in [(1, match["slot1_source"]), (2, match["slot2_source"])]:
            kind, ref_mn = source
            if kind not in ("winner", "loser"):
                continue  # ('seed', N) — no routing to set
            ref_match = matches[ref_mn]
            if kind == "winner":
                ref_match["winner_next_match_number"] = match["match_number"]
                ref_match["winner_next_slot"] = slot_num
            else:
                ref_match["loser_next_match_number"] = match["match_number"]
                ref_match["loser_next_slot"] = slot_num


# ──────────────────────────────────────────────────────────────────────────────
# Bracket HTML renderer
# ──────────────────────────────────────────────────────────────────────────────

_MATCH_H = 66   # px: two 30px player rows + 6px padding/border
_MATCH_W = 168  # px
_GAP_R1 = 10    # px between round-1 matches
_COL_GAP = 0    # connector already handles spacing


def _match_positions(n_r1: int, n_rounds: int) -> dict[int, list[float]]:
    """
    Compute top-edge pixel position for each match in each round (0-indexed).
    Returns {round_index: [top_px, top_px, ...]}.
    """
    H, G = _MATCH_H, _GAP_R1
    pos: dict[int, list[float]] = {}
    for r in range(n_rounds):
        n = n_r1 // (2 ** r)
        col = []
        for i in range(n):
            if r == 0:
                top = i * (H + G)
            else:
                p = pos[r - 1]
                center = (p[2 * i] + H / 2 + p[2 * i + 1] + H / 2) / 2
                top = center - H / 2
            col.append(top)
        pos[r] = col
    return pos


def _player_html(name: str | None, paid: bool, is_winner: bool, is_loser: bool) -> str:
    if name is None:
        return f'<div class="pb-player tbd">TBD</div>'
    cls = "pb-player"
    if is_winner:
        cls += " winner"
    elif is_loser:
        cls += " loser"
    dot_cls = "paid-dot" if paid else "unpaid-dot"
    return (
        f'<div class="{cls}">'
        f'<span class="pb-name">{name}</span>'
        f'<span class="{dot_cls}"></span>'
        f'</div>'
    )


def render_bracket_html(
    sections: dict,
    competitors: dict,
) -> str:
    """
    Build the bracket HTML.

    sections: {
        'winners': [[match_dict, ...], [match_dict, ...], ...],   # rounds
        'losers':  [...],
        'grand_final': [...],
    }
    competitors: {
        competitor_id: {
            'display_name': str,
            'paid': bool,
        }
    }

    Each match_dict needs:
      id, status, competitor1_id, competitor2_id, winner_id
    """
    css = f"""
<style>
.pb-wrap {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    overflow-x: auto;
    padding: 12px 4px 20px;
    background: transparent;
    -webkit-overflow-scrolling: touch;
}}
.pb-section-label {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #6B7280;
    margin: 16px 0 10px 4px;
}}
.pb-rounds {{
    display: flex;
    flex-direction: row;
    align-items: flex-start;
    gap: 0;
    min-width: max-content;
}}
.pb-col {{
    position: relative;
    width: {_MATCH_W + 24}px;
    flex-shrink: 0;
}}
.pb-col-label {{
    font-size: 10px;
    font-weight: 600;
    color: #9CA3AF;
    text-align: center;
    margin-bottom: 8px;
    letter-spacing: .05em;
    text-transform: uppercase;
}}
.pb-match {{
    position: absolute;
    left: 12px;
    width: {_MATCH_W}px;
    border: 1.5px solid #374151;
    border-radius: 6px;
    overflow: hidden;
    background: #1F2937;
    transition: border-color .15s;
}}
.pb-match.ready {{
    border-color: #F59E0B;
    box-shadow: 0 0 0 2px rgba(245,158,11,.2);
}}
.pb-match.completed {{
    border-color: #374151;
    opacity: .8;
}}
.pb-match.pending {{
    border-color: #1F2937;
    background: #111827;
}}
.pb-player {{
    display: flex;
    align-items: center;
    padding: 7px 10px;
    border-bottom: 1px solid #374151;
    font-size: 13px;
    color: #D1D5DB;
    min-height: 30px;
    gap: 6px;
}}
.pb-player:last-child {{ border-bottom: none; }}
.pb-player.winner {{
    background: rgba(34,197,94,.12);
    color: #4ADE80;
    font-weight: 600;
}}
.pb-player.loser {{ color: #6B7280; }}
.pb-player.tbd {{ color: #374151; font-style: italic; font-size: 12px; }}
.pb-name {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.paid-dot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: #22C55E; flex-shrink: 0;
    title: "Paid";
}}
.unpaid-dot {{
    width: 7px; height: 7px; border-radius: 50%;
    background: #EF4444; flex-shrink: 0;
    title: "Unpaid";
}}
</style>
"""

    def render_section(label: str, rounds: list[list[dict]]) -> str:
        if not rounds:
            return ""
        n_r1 = len(rounds[0])
        n_rounds = len(rounds)
        pos = _match_positions(n_r1, n_rounds)
        # total height of the first-round column
        total_h = n_r1 * _MATCH_H + max(0, n_r1 - 1) * _GAP_R1 + _MATCH_H  # extra pad

        out = f'<div class="pb-section-label">{label}</div>'
        out += '<div class="pb-rounds">'

        round_labels = {
            "winners": lambda r, tot: (
                "Final" if r == tot - 1 else
                "Semis" if r == tot - 2 else
                f"Round {r + 1}"
            ),
            "losers": lambda r, tot: f"LB R{r + 1}",
            "grand_final": lambda r, tot: (
                "Grand Final" if r == 0 else "Grand Final — Reset"
            ),
        }
        # Determine section type from label
        sec_key = "grand_final" if "Grand" in label else ("losers" if "Losers" in label else "winners")
        lbl_fn = round_labels[sec_key]

        for r_idx, round_matches in enumerate(rounds):
            col_h = total_h + 24  # label height offset
            out += f'<div class="pb-col" style="height:{col_h}px;">'
            out += f'<div class="pb-col-label">{lbl_fn(r_idx, n_rounds)}</div>'

            for m_idx, match in enumerate(round_matches):
                top_px = pos[r_idx][m_idx] + 24  # offset for label
                c1 = competitors.get(match.get("competitor1_id") or "")
                c2 = competitors.get(match.get("competitor2_id") or "")
                w_id = match.get("winner_id")
                status = match.get("status", "pending")

                n1 = c1["display_name"] if c1 else None
                n2 = c2["display_name"] if c2 else None
                p1 = c1["paid"] if c1 else False
                p2 = c2["paid"] if c2 else False
                c1_id = match.get("competitor1_id")
                c2_id = match.get("competitor2_id")

                row1 = _player_html(n1, p1, c1_id == w_id and w_id, c1_id != w_id and w_id and n1)
                row2 = _player_html(n2, p2, c2_id == w_id and w_id, c2_id != w_id and w_id and n2)

                out += (
                    f'<div class="pb-match {status}" style="top:{top_px:.0f}px;">'
                    f'{row1}{row2}'
                    f'</div>'
                )
            out += '</div>'  # col

        out += '</div>'  # rounds
        return out

    html = f'<div class="pb-wrap">'
    if "winners" in sections:
        html += render_section("Winners Bracket", sections["winners"])
    if "losers" in sections and any(sections["losers"]):
        html += render_section("Losers Bracket", sections["losers"])
    if "grand_final" in sections and any(sections["grand_final"]):
        html += render_section("Grand Final", sections["grand_final"])
    html += '</div>'

    return css + html
