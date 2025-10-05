-- db/schema.sql

CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cards (
  id BIGSERIAL PRIMARY KEY,
  owner_id BIGINT REFERENCES users(user_id),
  title TEXT NOT NULL,
  series TEXT,
  version TEXT,
  batch TEXT,
  rarity TEXT, -- Common, Rare, SR, SSR, UR
  image_url TEXT,
  raw_embed JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS submissions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES users(user_id),
  card_id BIGINT REFERENCES cards(id),
  currency TEXT,             -- e.g. "BS/MS" or "PayPal"
  rate TEXT,                 -- e.g. "200:1"
  queue TEXT,                -- "Normal", "Skip", "Card Maker"
  status TEXT,               -- "Pending", "Accepted", "Denied"
  moderator_id BIGINT,
  moderator_reason TEXT,
  target_channel_id BIGINT,
  forum_thread_id BIGINT,    -- thread id when posted
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS batches (
  id BIGSERIAL PRIMARY KEY,
  date DATE NOT NULL,
  status TEXT,               -- "Open", "Closed"
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS batch_items (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT REFERENCES batches(id),
  submission_id BIGINT REFERENCES submissions(id),
  rarity TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_queue ON submissions(queue);
