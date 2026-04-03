-- Pool Brackets — Supabase schema
-- Run this in the Supabase SQL editor before first deploy.

-- Players registry (shared across all tournaments)
CREATE TABLE players (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,  -- enforced "First L" format
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Tournaments
CREATE TABLE tournaments (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name                TEXT NOT NULL,
    format              TEXT NOT NULL CHECK (format IN ('singles_se', 'singles_de', 'doubles_se', 'doubles_de')),
    status              TEXT NOT NULL DEFAULT 'setup' CHECK (status IN ('setup', 'active', 'completed')),
    gf_reset_enabled    BOOLEAN DEFAULT TRUE,
    gf_reset_used       BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

-- Competitors in a tournament (individual player for singles/double_elim, or pair for doubles)
CREATE TABLE tournament_competitors (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tournament_id   UUID NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    player_id       UUID NOT NULL REFERENCES players(id),
    partner_id      UUID REFERENCES players(id),          -- doubles only; NULL for singles
    display_name    TEXT NOT NULL,                        -- "Alex G" or "Alex G / Sam T"
    seed            INTEGER,                              -- set when tournament starts
    paid            BOOLEAN DEFAULT FALSE,
    bracket_status  TEXT DEFAULT 'winners'
                    CHECK (bracket_status IN ('winners', 'losers', 'eliminated', 'champion')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tournament_id, player_id)
);

-- Matches — one row per match in the bracket
-- Routing columns (winner_next_match_id, loser_next_match_id) are stored as UUID without FK
-- to avoid circular dependency; the app maintains referential integrity.
CREATE TABLE matches (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tournament_id       UUID NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    match_number        INTEGER NOT NULL,       -- sequential within tournament, used for routing
    round_number        INTEGER NOT NULL,
    bracket             TEXT NOT NULL CHECK (bracket IN ('winners', 'losers', 'grand_final')),

    -- Competitors (NULL = TBD or bye slot)
    competitor1_id      UUID REFERENCES tournament_competitors(id),
    competitor2_id      UUID REFERENCES tournament_competitors(id),
    slot1_is_bye        BOOLEAN DEFAULT FALSE,  -- this slot is permanently a bye (no real player)
    slot2_is_bye        BOOLEAN DEFAULT FALSE,

    winner_id           UUID REFERENCES tournament_competitors(id),
    is_bye              BOOLEAN DEFAULT FALSE,  -- true if the whole match auto-advances
    gf_is_reset         BOOLEAN DEFAULT FALSE,  -- true for the grand final reset match

    status              TEXT DEFAULT 'pending'
                        CHECK (status IN ('pending', 'ready', 'completed')),

    -- Routing: where the winner/loser go next
    -- NULL = no next match (tournament end or elimination)
    winner_next_match_id    UUID,   -- no FK — resolved by app
    winner_next_slot        INTEGER CHECK (winner_next_slot IN (1, 2)),
    loser_next_match_id     UUID,
    loser_next_slot         INTEGER CHECK (loser_next_slot IN (1, 2)),

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tournament_id, match_number)
);

-- Indexes for common lookups
CREATE INDEX idx_matches_tournament ON matches(tournament_id);
CREATE INDEX idx_matches_status     ON matches(tournament_id, status);
CREATE INDEX idx_competitors_tournament ON tournament_competitors(tournament_id);
CREATE INDEX idx_competitors_player ON tournament_competitors(player_id);
