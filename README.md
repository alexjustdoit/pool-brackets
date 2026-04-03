# 🎱 Pool Brackets

Tournament bracket manager for pool/billiards. Run tournaments, track results, and view stats — hosted free on Streamlit Community Cloud with persistent Supabase storage.

## Features

- **Single elimination, double elimination, doubles (2v2)** formats
- **Byes** for non-power-of-2 entry counts (auto-handled)
- **Double elimination** with correct WB/LB routing and optional grand final reset match
- **Name format enforcement** — "First L" style with fuzzy auto-suggest against known players
- **Payment tracking** — tap to toggle paid/unpaid per player per tournament
- **Doubles eligibility check** — warns if both partners won a singles tournament in the last 12 months
- **Stats dashboard** — win rates, nemesis tracking, typical elimination stage, recent form
- **Historical data import** — CSV upload for past tournament results

## Stack

- **Frontend**: Streamlit (Streamlit Community Cloud)
- **Database**: Supabase (PostgreSQL)
- **Name matching**: rapidfuzz
- **Charts**: Plotly

## Setup

### 1. Database

Run `schema.sql` in the Supabase SQL editor (Dashboard → SQL Editor).

### 2. Environment variables

Copy `.env.example` to `.env`:
```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

### 3. Run locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

### 4. Deploy to Streamlit Community Cloud

- Connect the GitHub repo at share.streamlit.io
- Set `SUPABASE_URL` and `SUPABASE_KEY` as secrets
- After deploy, add app URL as `APP_URL` in GitHub Actions secrets for keepalive

## Bracket formats

### Single elimination
Standard knockout — lose once, you're out.

### Double elimination
- Win → stay in Winners Bracket
- Lose in WB → drop to Losers Bracket
- Lose in LB → eliminated
- Grand final: WB champion vs LB champion. If LB champ wins, a reset match is played (default behavior, can be disabled per tournament).

### Doubles (2v2)
Same bracket formats as above, with teams of 2. Eligibility rule: two players who have both won a singles tournament in the last 12 months cannot partner each other.

## Backlog

- First-to-X games match settings (e.g. first to 3 games wins the match)
- Match scores (optional game-by-game tracking)
- Undo last result
- Export bracket as PDF/image
- Push notifications for "your match is ready"
