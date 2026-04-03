"""
Stats calculations for the player dashboard.
"""

from supabase import Client
from collections import defaultdict, Counter


def get_player_stats(sb: Client, player_id: str) -> dict:
    """
    Return a comprehensive stats dict for one player across all tournaments.

    Returns:
    {
      'tournaments_played': int,
      'tournaments_won': int,
      'win_rate': float,       # 0–1
      'match_wins': int,
      'match_losses': int,
      'match_win_rate': float,
      'nemesis': {'name': str, 'losses': int} | None,
      'best_victim': {'name': str, 'wins': int} | None,
      'typical_stage': str,    # e.g. "Losers Bracket R2"
      'stage_breakdown': dict, # stage → count
      'recent_form': list[str], # last 10 matches: 'W' or 'L'
      'format_stats': {
          'single_elim': {'played': int, 'won': int},
          'double_elim': {'played': int, 'won': int},
          'doubles':     {'played': int, 'won': int},
      },
    }
    """
    # Get competitor rows for this player
    comp_rows = (
        sb.table("tournament_competitors")
        .select("id, bracket_status, seed, tournament_id, tournaments(name, format, status, completed_at)")
        .eq("player_id", player_id)
        .execute()
        .data
    )

    comp_ids = [c["id"] for c in comp_rows]
    if not comp_ids:
        return _empty_stats()

    # Get all matches involving this player
    matches_as_c1 = (
        sb.table("matches")
        .select("*")
        .in_("competitor1_id", comp_ids)
        .eq("status", "completed")
        .execute()
        .data
    )
    matches_as_c2 = (
        sb.table("matches")
        .select("*")
        .in_("competitor2_id", comp_ids)
        .eq("status", "completed")
        .execute()
        .data
    )

    all_matches = matches_as_c1 + matches_as_c2
    # Deduplicate (shouldn't happen but safe)
    seen = set()
    deduped = []
    for m in all_matches:
        if m["id"] not in seen:
            seen.add(m["id"])
            deduped.append(m)
    all_matches = deduped

    # Build comp_id → tournament_id map
    comp_to_tournament = {c["id"]: c["tournament_id"] for c in comp_rows}
    comp_to_format = {
        c["id"]: (c.get("tournaments") or {}).get("format", "single_elim")
        for c in comp_rows
    }

    # Tournament-level stats
    t_played = len(comp_rows)
    t_won = sum(1 for c in comp_rows if c["bracket_status"] == "champion")

    # Match-level stats
    match_wins = 0
    match_losses = 0
    opponent_loss_counter: Counter = Counter()  # opponent_comp_id → our losses
    opponent_win_counter: Counter = Counter()   # opponent_comp_id → our wins
    recent: list[tuple] = []  # (created_at, 'W'/'L')
    stage_counter: Counter = Counter()

    for m in all_matches:
        if m["is_bye"]:
            continue
        c1, c2 = m["competitor1_id"], m["competitor2_id"]
        w = m["winner_id"]

        # Find which side we're on
        our_comp_id = c1 if c1 in comp_ids else c2
        opp_comp_id = c2 if c1 in comp_ids else c1

        if w == our_comp_id:
            match_wins += 1
            if opp_comp_id:
                opponent_win_counter[opp_comp_id] += 1
            result = "W"
        else:
            match_losses += 1
            if opp_comp_id:
                opponent_loss_counter[opp_comp_id] += 1
            result = "L"

        recent.append((m.get("created_at", ""), result))

        stage = f"{m['bracket'].replace('_', ' ').title()} R{m['round_number']}"
        if result == "L":
            stage_counter[stage] += 1

    # Resolve nemesis (most losses against a single opponent)
    nemesis = None
    if opponent_loss_counter:
        opp_id, loss_count = opponent_loss_counter.most_common(1)[0]
        if loss_count >= 2:
            name = _resolve_competitor_name(sb, opp_id)
            nemesis = {"name": name, "losses": loss_count}

    # Best victim (most wins against a single opponent)
    best_victim = None
    if opponent_win_counter:
        opp_id, win_count = opponent_win_counter.most_common(1)[0]
        if win_count >= 2:
            name = _resolve_competitor_name(sb, opp_id)
            best_victim = {"name": name, "wins": win_count}

    # Typical elimination stage
    typical_stage = stage_counter.most_common(1)[0][0] if stage_counter else "N/A"

    # Recent form (last 10 real matches, newest first)
    recent.sort(key=lambda x: x[0], reverse=True)
    recent_form = [r for _, r in recent[:10]]

    # Format stats
    format_stats: dict = {
        "single_elim": {"played": 0, "won": 0},
        "double_elim": {"played": 0, "won": 0},
        "doubles": {"played": 0, "won": 0},
    }
    for c in comp_rows:
        fmt = (c.get("tournaments") or {}).get("format", "single_elim")
        if fmt not in format_stats:
            fmt = "single_elim"
        format_stats[fmt]["played"] += 1
        if c["bracket_status"] == "champion":
            format_stats[fmt]["won"] += 1

    total_matches = match_wins + match_losses
    return {
        "tournaments_played": t_played,
        "tournaments_won": t_won,
        "win_rate": t_won / t_played if t_played else 0,
        "match_wins": match_wins,
        "match_losses": match_losses,
        "match_win_rate": match_wins / total_matches if total_matches else 0,
        "nemesis": nemesis,
        "best_victim": best_victim,
        "typical_stage": typical_stage,
        "stage_breakdown": dict(stage_counter),
        "recent_form": recent_form,
        "format_stats": format_stats,
    }


def get_leaderboard(sb: Client) -> list[dict]:
    """
    Return all players with their summary stats, sorted by tournaments won desc.
    """
    players = sb.table("players").select("*").order("name").execute().data
    rows = []
    for p in players:
        # Quick stats without full breakdown
        comps = (
            sb.table("tournament_competitors")
            .select("bracket_status")
            .eq("player_id", p["id"])
            .execute()
            .data
        )
        if not comps:
            continue
        played = len(comps)
        won = sum(1 for c in comps if c["bracket_status"] == "champion")
        rows.append({
            "id": p["id"],
            "name": p["name"],
            "played": played,
            "won": won,
            "win_rate": won / played if played else 0,
        })
    rows.sort(key=lambda x: (-x["won"], -x["win_rate"], x["name"]))
    return rows


def get_head_to_head(sb: Client, player1_id: str, player2_id: str) -> dict:
    """
    Return head-to-head record between two players.
    """
    # Get competitor ids for each player
    c1_ids = [
        r["id"] for r in
        sb.table("tournament_competitors").select("id").eq("player_id", player1_id).execute().data
    ]
    c2_ids = [
        r["id"] for r in
        sb.table("tournament_competitors").select("id").eq("player_id", player2_id).execute().data
    ]
    if not c1_ids or not c2_ids:
        return {"p1_wins": 0, "p2_wins": 0, "total": 0}

    # Matches where c1 vs c2
    matches = (
        sb.table("matches")
        .select("*")
        .eq("status", "completed")
        .execute()
        .data
    )
    p1_wins = 0
    p2_wins = 0
    for m in matches:
        mc1, mc2 = m["competitor1_id"], m["competitor2_id"]
        w = m["winner_id"]
        # Check if this match is between our two players
        p1_side = mc1 in c1_ids or mc2 in c1_ids
        p2_side = mc1 in c2_ids or mc2 in c2_ids
        if not (p1_side and p2_side):
            continue
        if w in c1_ids:
            p1_wins += 1
        elif w in c2_ids:
            p2_wins += 1

    return {"p1_wins": p1_wins, "p2_wins": p2_wins, "total": p1_wins + p2_wins}


def _resolve_competitor_name(sb: Client, competitor_id: str) -> str:
    rows = (
        sb.table("tournament_competitors")
        .select("display_name")
        .eq("id", competitor_id)
        .execute()
        .data
    )
    return rows[0]["display_name"] if rows else "Unknown"


def _empty_stats() -> dict:
    return {
        "tournaments_played": 0,
        "tournaments_won": 0,
        "win_rate": 0,
        "match_wins": 0,
        "match_losses": 0,
        "match_win_rate": 0,
        "nemesis": None,
        "best_victim": None,
        "typical_stage": "N/A",
        "stage_breakdown": {},
        "recent_form": [],
        "format_stats": {
            "single_elim": {"played": 0, "won": 0},
            "double_elim": {"played": 0, "won": 0},
            "doubles": {"played": 0, "won": 0},
        },
    }
