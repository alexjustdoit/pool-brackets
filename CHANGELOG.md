# Changelog

## v0.1.0 — 2026-04-02

Initial release.

### Features
- Single elimination, double elimination, doubles (2v2) tournament formats
- Byes for non-power-of-2 entry counts (auto-cascaded through LB)
- Double elimination bracket with correct WB/LB routing (cross-paired to avoid rematches)
- Grand final reset match (optional per tournament, default ON)
- Random seeding
- Name input: "First L" enforcement with fuzzy matching against existing players
- Payment tracking: tap-to-toggle paid/unpaid inline on roster and bracket
- Doubles eligibility warning: flags ineligible pairings (both partners won singles in last 12 months)
- Bracket visualization: rounds-as-columns layout, color-coded match status
- Match result entry: tap-to-select winner per ready match
- Stats dashboard: win rates, nemesis, best victim, typical elimination stage, recent form, format breakdown
- Historical data import: CSV upload with preview
- Leaderboard across all tournaments
- GitHub Actions keepalive for Streamlit Community Cloud
