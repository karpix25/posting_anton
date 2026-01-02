-- Brand Stats Table for Quota Management
-- Tracks published videos per brand per month

CREATE TABLE IF NOT EXISTS brand_stats (
  id SERIAL PRIMARY KEY,
  category VARCHAR(100) NOT NULL,
  brand VARCHAR(100) NOT NULL,
  month VARCHAR(7) NOT NULL,  -- Format: 'YYYY-MM' (e.g., '2026-01')
  published_count INT DEFAULT 0,
  quota INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(category, brand, month)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_brand_stats_month ON brand_stats(month);
CREATE INDEX IF NOT EXISTS idx_brand_stats_category_brand ON brand_stats(category, brand);
CREATE INDEX IF NOT EXISTS idx_brand_stats_lookup ON brand_stats(category, brand, month);

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_brand_stats_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER brand_stats_updated_at
BEFORE UPDATE ON brand_stats
FOR EACH ROW
EXECUTE FUNCTION update_brand_stats_timestamp();
