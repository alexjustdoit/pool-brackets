"""
All Supabase database operations for Pool Brackets.
"""

import random
import math
from datetime import datetime, timezone
from supabase import create_client, Client
from utils.bracket import generate_bracket, next_power_of_2, seeding_order


# ──────────────────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────────────────

def get_client(url: str, key: str) -> Client:
    return create_client(url, key)


# ──────────────────────────────────────────────────────────────────────────────
# Players
# ──────────────────────────────────────────────────────────────────────────────

def get_all_players(sb: Client) -> list[dict]:
    return sb.table("players").select("*").order("name").execute().data


def get_or_create_player(sb: Client, name: str) -> dict:
    """Return existing player with this name, or create a new one."""
    rows = sb.table("players").select("*").eq("name", name).execute().data
    if rows:
        return rows[0]
    return sb.table("players").insert({"name": name}).execute().data[0]


# ──────────────────────────────────────────────────────────────────────────────
# Tournaments
# ──────────────────────────────────────────────────────────────────────────────

def get_all_tournaments(sb: Client) -> list[dict]:
    return (
        sb.table("tournaments")
        .select("*")
        .order("created_at", desc=True)
        .execute()
        .data
    )


def get_tournament(sb: Client, tournament_id: str) -> dict | None:
    rows = sb.table("tournaments").select("*").eq("id", tournament_id).execute().data
    return rows[0] if rows else None


def create_tournament(sb: Client, name: str, format: str, gf_reset_enabled: bool = True) -> dict:
    return (
        sb.table("tournaments")
        .insert({
            "name": name,
            "format": format,
            "gf_reset_enabled": gf_reset_enabled,
        })
        .execute()
        .data[0]
    )


def _update_tournament(sb: Client, tournament_id: str, updates: dict) -> None:
    sb.table("tournaments").update(updates).eq("id", tournament_id).execute()


# ──────────────────────────────────────────────────────────────────────────────
# Tournament competitors
# ──────────────────────────────────────────────────────────────────────────────

def get_competitors(sb: Client, tournament_id: str) -> list[dict]:
    return (
        sb.table("tournament_competitors")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("created_at")
        .execute()
        .data
    )


def add_competitor(
    sb: Client,
    tournament_id: str,
    player_id: str,
    partner_id: str | None = None,
    display_name: str = "",
    paid: bool = False,
) -> dict:
    return (
        sb.table("tournament_competitors")
        .insert({
            "tournament_id": tournament_id,
            "player_id": player_id,
            "partner_id": partner_id,
            "display_name": display_name,
            "paid": paid,
        })
        .execute()
        .data[0]
    )


def remove_competitor(sb: Client, competitor_id: str) -> None:
    sb.table("tournament_competitors").delete().eq("id", competitor_id).execute()


def toggle_paid(sb: Client, competitor_id: str, paid: bool) -> None:
    sb.table("tournament_competitors").update({"paid": paid}).eq("id", competitor_id).execute()


def check_doubles_eligibility(sb: Client, player_id: str) -> bool:
    """
    Return True if this player has won a singles (single_elim or double_elim)
    tournament in the last 12 months.
    """
    rows = (
        sb.table("tournament_competitors")
        .select("id, bracket_status, tournament_id, tournaments(format, completed_at)")
        .eq("player_id", player_id)
        .eq("bracket_status", "champion")
        .execute()
        .data
    )
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    for row in rows:
        t = row.get("tournaments", {})
        if t.get("format") in ("singles_se", "singles_de"):
            completed = t.get("completed_at")
            if completed:
                try:
                    dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                    if dt >= cutoff:
                        return True
                except Exception:
                    pass
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Starting a tournament (bracket generation)
# ──────────────────────────────────────────────────────────────────────────────

def start_tournament(sb: Client, tournament_id: str) -> None:
    """
    Randomly seed competitors, generate the bracket, insert all matches,
    wire routing, auto-complete byes, and set tournament status → active.
    """
    tournament = get_tournament(sb, tournament_id)
    if not tournament or tournament["status"] != "setup":
        raise ValueError("Tournament is not in setup state")

    competitors = get_competitors(sb, tournament_id)
    n = len(competitors)
    if n < 2:
        raise ValueError("Need at least 2 competitors to start")

    # Randomize seeding
    random.shuffle(competitors)
    for i, comp in enumerate(competitors):
        sb.table("tournament_competitors").update({"seed": i + 1}).eq("id", comp["id"]).execute()

    # Determine bracket type from format string
    fmt = tournament["format"]
    bracket_format = "single_elim" if fmt.endswith("_se") else "double_elim"

    match_specs = generate_bracket(n, bracket_format)

    # Build seed → competitor_id map
    seed_map = {i + 1: competitors[i]["id"] for i in range(n)}
    size = next_power_of_2(n)

    # ── Insert all matches (routing wired in second pass) ──────────────────
    # First pass: insert with competitor slots where known (WB-R1 only)
    local_to_db: dict[int, str] = {}

    for spec in match_specs:
        s1_kind, s1_val = spec["slot1_source"]
        s2_kind, s2_val = spec["slot2_source"]

        comp1_id = None
        comp2_id = None
        slot1_is_bye = False
        slot2_is_bye = False

        if s1_kind == "seed":
            comp1_id = seed_map.get(s1_val)
            slot1_is_bye = s1_val > n
        if s2_kind == "seed":
            comp2_id = seed_map.get(s2_val)
            slot2_is_bye = s2_val > n

        is_bye = slot1_is_bye or slot2_is_bye
        if comp1_id is not None and comp2_id is not None and not is_bye:
            status = "ready"
        elif is_bye and (comp1_id is not None or comp2_id is not None):
            status = "ready"  # will be auto-completed
        elif slot1_is_bye and slot2_is_bye:
            status = "ready"  # double-bye
        else:
            status = "pending"

        row = sb.table("matches").insert({
            "tournament_id": tournament_id,
            "match_number": spec["match_number"],
            "round_number": spec["round_number"],
            "bracket": spec["bracket"],
            "competitor1_id": comp1_id,
            "competitor2_id": comp2_id,
            "slot1_is_bye": slot1_is_bye,
            "slot2_is_bye": slot2_is_bye,
            "is_bye": is_bye,
            "status": status,
        }).execute().data[0]

        local_to_db[spec["match_number"]] = row["id"]

    # Second pass: wire routing UUIDs
    for spec in match_specs:
        updates = {}
        if spec["winner_next_match_number"]:
            updates["winner_next_match_id"] = local_to_db[spec["winner_next_match_number"]]
            updates["winner_next_slot"] = spec["winner_next_slot"]
        if spec["loser_next_match_number"]:
            updates["loser_next_match_id"] = local_to_db[spec["loser_next_match_number"]]
            updates["loser_next_slot"] = spec["loser_next_slot"]
        if updates:
            sb.table("matches").update(updates).eq("id", local_to_db[spec["match_number"]]).execute()

    # Process byes (cascades until stable)
    _process_byes(sb, tournament_id)

    _update_tournament(sb, tournament_id, {"status": "active"})


# ──────────────────────────────────────────────────────────────────────────────
# Bye processing
# ──────────────────────────────────────────────────────────────────────────────

def _process_byes(sb: Client, tournament_id: str) -> None:
    """
    Iteratively auto-complete any match that has one (or two) bye slots
    and the non-bye slot is filled. Cascades until stable.
    """
    while True:
        # Matches that are ready/pending and have at least one bye slot
        rows = (
            sb.table("matches")
            .select("*")
            .eq("tournament_id", tournament_id)
            .neq("status", "completed")
            .or_("slot1_is_bye.eq.true,slot2_is_bye.eq.true")
            .execute()
            .data
        )

        changed = False
        for match in rows:
            s1_bye = match["slot1_is_bye"]
            s2_bye = match["slot2_is_bye"]
            c1 = match["competitor1_id"]
            c2 = match["competitor2_id"]

            winner_id = None
            should_auto = False

            if s1_bye and s2_bye:
                # Double bye: no winner — propagate bye further
                should_auto = True
                winner_id = None
            elif s1_bye and c2 is not None:
                should_auto = True
                winner_id = c2
            elif s2_bye and c1 is not None:
                should_auto = True
                winner_id = c1

            if should_auto:
                _auto_advance(sb, match, winner_id)
                changed = True

        if not changed:
            break


def _auto_advance(sb: Client, match: dict, winner_id: str | None) -> None:
    """
    Complete a bye match: mark it completed, advance winner, propagate
    the bye to the loser slot of the next match.
    """
    sb.table("matches").update({
        "winner_id": winner_id,
        "status": "completed",
    }).eq("id", match["id"]).execute()

    # Advance winner to next match
    if match["winner_next_match_id"] and winner_id:
        slot = match["winner_next_slot"]
        sb.table("matches").update({
            f"competitor{slot}_id": winner_id,
        }).eq("id", match["winner_next_match_id"]).execute()
        _maybe_activate(sb, match["winner_next_match_id"])

    # Propagate bye to the loser slot of the next match (no real loser)
    if match["loser_next_match_id"]:
        slot = match["loser_next_slot"]
        # Mark that slot as a bye in the receiving match
        sb.table("matches").update({
            f"slot{slot}_is_bye": True,
            "is_bye": True,
        }).eq("id", match["loser_next_match_id"]).execute()
        _maybe_activate(sb, match["loser_next_match_id"])


def _maybe_activate(sb: Client, match_id: str) -> None:
    """Set a match to 'ready' if both slots are now filled or are byes."""
    rows = sb.table("matches").select("*").eq("id", match_id).execute().data
    if not rows:
        return
    m = rows[0]
    if m["status"] == "completed":
        return
    s1_ready = m["competitor1_id"] is not None or m["slot1_is_bye"]
    s2_ready = m["competitor2_id"] is not None or m["slot2_is_bye"]
    if s1_ready and s2_ready:
        sb.table("matches").update({"status": "ready"}).eq("id", match_id).execute()


# ──────────────────────────────────────────────────────────────────────────────
# Recording match results
# ──────────────────────────────────────────────────────────────────────────────

def record_result(sb: Client, match_id: str, winner_competitor_id: str) -> None:
    """
    Record the result of a match and advance the bracket.
    Handles double elimination routing, GF reset logic, and tournament completion.
    """
    rows = sb.table("matches").select("*").eq("id", match_id).execute().data
    if not rows:
        raise ValueError(f"Match {match_id} not found")
    match = rows[0]

    if match["status"] == "completed":
        raise ValueError("Match already completed")

    # Determine loser
    c1 = match["competitor1_id"]
    c2 = match["competitor2_id"]
    loser_id = c2 if winner_competitor_id == c1 else c1

    # Mark match completed
    sb.table("matches").update({
        "winner_id": winner_competitor_id,
        "status": "completed",
    }).eq("id", match_id).execute()

    # ── Grand Final handling ────────────────────────────────────────────────
    if match["bracket"] == "grand_final":
        _handle_gf_result(sb, match, winner_competitor_id, loser_id)
        return

    # ── Advance winner ──────────────────────────────────────────────────────
    if match["winner_next_match_id"]:
        slot = match["winner_next_slot"]
        sb.table("matches").update({
            f"competitor{slot}_id": winner_competitor_id,
        }).eq("id", match["winner_next_match_id"]).execute()
        _maybe_activate(sb, match["winner_next_match_id"])

    # ── Route loser ─────────────────────────────────────────────────────────
    if loser_id:
        if match["loser_next_match_id"]:
            slot = match["loser_next_slot"]
            sb.table("matches").update({
                f"competitor{slot}_id": loser_id,
            }).eq("id", match["loser_next_match_id"]).execute()
            # Update bracket_status to 'losers' when dropping from WB
            if match["bracket"] == "winners":
                sb.table("tournament_competitors").update({
                    "bracket_status": "losers",
                }).eq("id", loser_id).execute()
            _maybe_activate(sb, match["loser_next_match_id"])
        else:
            # No loser next match → eliminated
            sb.table("tournament_competitors").update({
                "bracket_status": "eliminated",
            }).eq("id", loser_id).execute()

    # Process any newly-triggered byes
    _process_byes(sb, match["tournament_id"])


def _handle_gf_result(
    sb: Client,
    match: dict,
    winner_id: str,
    loser_id: str | None,
) -> None:
    tournament_id = match["tournament_id"]
    tournament = get_tournament(sb, tournament_id)

    if match["gf_is_reset"]:
        # GF reset completed → tournament over
        _finish_tournament(sb, tournament_id, winner_id, loser_id)
        return

    # GF game 1:
    # slot1 = WB champ (0 losses), slot2 = LB champ (1 loss)
    wb_champ_id = match["competitor1_id"]
    lb_champ_id = match["competitor2_id"]

    if winner_id == lb_champ_id and tournament["gf_reset_enabled"] and not tournament["gf_reset_used"]:
        # LB champ wins GF game 1 — both have 1 loss now → reset match needed
        _create_gf_reset(sb, tournament_id, wb_champ_id, lb_champ_id)
        _update_tournament(sb, tournament_id, {"gf_reset_used": True})
    else:
        # WB champ wins, OR reset disabled/already used → tournament over
        _finish_tournament(sb, tournament_id, winner_id, loser_id)


def _create_gf_reset(
    sb: Client,
    tournament_id: str,
    wb_champ_id: str,
    lb_champ_id: str,
) -> None:
    """Create and activate the GF reset match."""
    # Find the highest existing match_number and add 1
    rows = (
        sb.table("matches")
        .select("match_number")
        .eq("tournament_id", tournament_id)
        .order("match_number", desc=True)
        .limit(1)
        .execute()
        .data
    )
    next_num = (rows[0]["match_number"] + 1) if rows else 1

    sb.table("matches").insert({
        "tournament_id": tournament_id,
        "match_number": next_num,
        "round_number": 2,
        "bracket": "grand_final",
        "competitor1_id": wb_champ_id,
        "competitor2_id": lb_champ_id,
        "gf_is_reset": True,
        "status": "ready",
    }).execute()


def _finish_tournament(
    sb: Client,
    tournament_id: str,
    champion_id: str,
    runner_up_id: str | None,
) -> None:
    if champion_id:
        sb.table("tournament_competitors").update({
            "bracket_status": "champion",
        }).eq("id", champion_id).execute()
    if runner_up_id:
        sb.table("tournament_competitors").update({
            "bracket_status": "eliminated",
        }).eq("id", runner_up_id).execute()
    _update_tournament(sb, tournament_id, {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Changing a recorded result
# ──────────────────────────────────────────────────────────────────────────────

def _get_match(sb: Client, match_id: str) -> dict | None:
    rows = sb.table("matches").select("*").eq("id", match_id).execute().data
    return rows[0] if rows else None


def change_result(sb: Client, match_id: str, new_winner_id: str) -> None:
    """
    Change the result of a completed match.

    Only allowed if no downstream match (the winner's next or the loser's next)
    has already been completed — otherwise the bracket has moved on too far to
    safely rewind.

    Steps:
      1. Validate downstream matches are still open.
      2. Clear the old winner / loser from their downstream slots.
      3. Reset bracket_status on both competitors.
      4. Delete any GF reset match that was created as a consequence.
      5. Un-complete the match and call record_result() with the new winner.
    """
    match = _get_match(sb, match_id)
    if not match or match["status"] != "completed":
        raise ValueError("Match is not completed.")

    old_winner_id = match["winner_id"]
    if old_winner_id == new_winner_id:
        return  # nothing to do

    c1, c2 = match["competitor1_id"], match["competitor2_id"]
    old_loser_id = c2 if old_winner_id == c1 else c1

    # ── Guard: downstream matches must not be completed ──────────────────────
    for next_id in [match["winner_next_match_id"], match["loser_next_match_id"]]:
        if not next_id:
            continue
        nxt = _get_match(sb, next_id)
        if nxt and nxt["status"] == "completed":
            raise ValueError(
                "Can't change this result — a later match has already been played. "
                "Change that result first."
            )

    # ── Remove old winner from their next match slot ─────────────────────────
    if match["winner_next_match_id"]:
        slot = match["winner_next_slot"]
        sb.table("matches").update({
            f"competitor{slot}_id": None,
            "status": "pending",
        }).eq("id", match["winner_next_match_id"]).execute()
        _maybe_activate(sb, match["winner_next_match_id"])

    # ── Remove old loser from their next match slot ──────────────────────────
    if match["loser_next_match_id"]:
        slot = match["loser_next_slot"]
        sb.table("matches").update({
            f"competitor{slot}_id": None,
        }).eq("id", match["loser_next_match_id"]).execute()
        _maybe_activate(sb, match["loser_next_match_id"])

    # ── Reset competitor bracket statuses ────────────────────────────────────
    if old_winner_id:
        # Old winner was either WB player or LB player — put them back
        prior_status = "losers" if match["bracket"] == "losers" else "winners"
        sb.table("tournament_competitors").update({
            "bracket_status": prior_status,
        }).eq("id", old_winner_id).execute()
    if old_loser_id:
        prior_status = "losers" if match["bracket"] == "losers" else "winners"
        sb.table("tournament_competitors").update({
            "bracket_status": prior_status,
        }).eq("id", old_loser_id).execute()

    # ── GF-specific cleanup ──────────────────────────────────────────────────
    if match["bracket"] == "grand_final":
        # Delete any reset match that was spun up from this result
        sb.table("matches").delete().eq(
            "tournament_id", match["tournament_id"]
        ).eq("gf_is_reset", True).execute()
        _update_tournament(sb, match["tournament_id"], {
            "gf_reset_used": False,
            "status": "active",
            "completed_at": None,
        })

    # ── Reset the match itself and re-record ─────────────────────────────────
    sb.table("matches").update({
        "winner_id": None,
        "status": "ready",
    }).eq("id", match_id).execute()

    record_result(sb, match_id, new_winner_id)


# ──────────────────────────────────────────────────────────────────────────────
# Bracket read queries
# ──────────────────────────────────────────────────────────────────────────────

def get_bracket_data(sb: Client, tournament_id: str) -> dict:
    """
    Returns:
      {
        'matches': [match_dict, ...],    -- all matches, sorted by bracket+round+match_number
        'competitors': {id: comp_dict},  -- keyed by competitor id
        'ready_matches': [match_dict, ...],
      }
    """
    matches = (
        sb.table("matches")
        .select("*")
        .eq("tournament_id", tournament_id)
        .order("match_number")
        .execute()
        .data
    )

    comp_rows = get_competitors(sb, tournament_id)
    competitors = {c["id"]: c for c in comp_rows}

    ready = [m for m in matches if m["status"] == "ready"]

    return {"matches": matches, "competitors": competitors, "ready_matches": ready}


def organize_bracket_for_render(matches: list[dict]) -> dict:
    """
    Group matches into sections for the bracket renderer.
    Returns:
      {
        'winners': [[round1_matches], [round2_matches], ...],
        'losers':  [[...], ...],
        'grand_final': [[gf_game1], [gf_reset (if exists)]],
      }
    """
    from collections import defaultdict

    by_bracket: dict[str, dict[int, list]] = {
        "winners": defaultdict(list),
        "losers": defaultdict(list),
        "grand_final": defaultdict(list),
    }
    for m in matches:
        by_bracket[m["bracket"]][m["round_number"]].append(m)

    def to_rounds(d: dict) -> list[list]:
        if not d:
            return []
        for rnd in d.values():
            rnd.sort(key=lambda m: m["match_number"])
        return [d[r] for r in sorted(d)]

    return {
        "winners": to_rounds(by_bracket["winners"]),
        "losers": to_rounds(by_bracket["losers"]),
        "grand_final": to_rounds(by_bracket["grand_final"]),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Historical CSV import
# ──────────────────────────────────────────────────────────────────────────────

def import_historical(sb: Client, records: list[dict]) -> dict:
    """
    Import historical tournament results from a list of dicts.
    Expected keys: date, tournament_name, format, player_name, placement, paid

    Returns {'imported': N, 'errors': [str, ...]}
    """
    from collections import defaultdict

    errors = []
    imported = 0

    # Group by tournament
    by_tournament: dict[str, list] = defaultdict(list)
    for rec in records:
        key = f"{rec.get('date', '')}|{rec.get('tournament_name', '')}"
        by_tournament[key].append(rec)

    for key, rows in by_tournament.items():
        try:
            sample = rows[0]
            date_str = sample.get("date", "")
            t_name = sample.get("tournament_name", "")
            fmt = sample.get("format", "single_elim")
            if fmt not in ("single_elim", "double_elim", "doubles"):
                fmt = "single_elim"

            # Parse date
            try:
                completed_at = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                completed_at = None

            # Create tournament (already completed)
            t = sb.table("tournaments").insert({
                "name": t_name,
                "format": fmt,
                "status": "completed",
                "completed_at": completed_at,
                "gf_reset_enabled": True,
            }).execute().data[0]

            # Add competitors with placements recorded as bracket_status
            for row in rows:
                p_name = str(row.get("player_name", "")).strip()
                if not p_name:
                    continue
                paid = str(row.get("paid", "false")).lower() in ("true", "yes", "1")
                placement = row.get("placement", 99)
                try:
                    placement = int(placement)
                except (ValueError, TypeError):
                    placement = 99

                player = get_or_create_player(sb, p_name)
                status = "champion" if placement == 1 else "eliminated"

                try:
                    sb.table("tournament_competitors").insert({
                        "tournament_id": t["id"],
                        "player_id": player["id"],
                        "display_name": p_name,
                        "paid": paid,
                        "bracket_status": status,
                        "seed": placement,  # repurpose seed column to store placement
                    }).execute()
                    imported += 1
                except Exception as e:
                    errors.append(f"{t_name} / {p_name}: {e}")

        except Exception as e:
            errors.append(f"Tournament {key}: {e}")

    return {"imported": imported, "errors": errors}
