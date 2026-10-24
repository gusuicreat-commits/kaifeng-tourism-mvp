BEGIN;
CREATE TABLE scenic_parks (
  park_id text PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE scenic_schedule_sources (
  source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  park_id text NOT NULL REFERENCES scenic_parks(park_id),
  source_type text NOT NULL CHECK (source_type IN ('official_html','manual_csv')),
  source_ref text NOT NULL, source_updated_on date,
  fetched_at timestamptz NOT NULL, content_sha256 text NOT NULL,
  http_status integer, fetch_status text NOT NULL CHECK (fetch_status IN ('received','http_error','network_error','rejected','blocked')),
  error_code text, raw_snapshot bytea, raw_media_type text NOT NULL DEFAULT 'text/html',
  evidence_sha256 text, evidence_storage_ref text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scenic_source_park_time ON scenic_schedule_sources(park_id, fetched_at DESC);
CREATE TABLE scenic_schedules (
  schedule_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  park_id text NOT NULL REFERENCES scenic_parks(park_id),
  service_date date NOT NULL, version integer NOT NULL,
  source_id bigint NOT NULL REFERENCES scenic_schedule_sources(source_id), source_fingerprint text NOT NULL,
  status text NOT NULL CHECK (status IN ('approved_schedule','stale_source','review_required','unavailable')),
  source_updated_on date, effective_from date, effective_to date,
  applicability_verified boolean NOT NULL DEFAULT false,
  completeness_verified boolean NOT NULL DEFAULT false,
  previous_schedule_id bigint REFERENCES scenic_schedules(schedule_id),
  review_reason text NOT NULL, reviewer text, reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(park_id, service_date, version),
  UNIQUE(schedule_id, park_id, service_date),
  UNIQUE(park_id, service_date, source_id),
  CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from),
  CHECK (status <> 'approved_schedule' OR (applicability_verified AND completeness_verified AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX scenic_schedule_public ON scenic_schedules(park_id, service_date, version DESC);
CREATE TABLE scenic_performances (
  performance_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schedule_id bigint NOT NULL REFERENCES scenic_schedules(schedule_id) ON DELETE CASCADE,
  park_id text NOT NULL, service_date date NOT NULL,
  program_name text NOT NULL, venue_name text NOT NULL, area text,
  start_time time, duration_minutes integer CHECK (duration_minutes IS NULL OR duration_minutes > 0),
  end_time time, entry_deadline time,
  requires_booking boolean, weather_sensitive boolean,
  operating_status text NOT NULL CHECK (operating_status IN ('scheduled','cancelled','unknown')),
  schedule_note text NOT NULL DEFAULT '', natural_key text NOT NULL,
  source_fingerprint text NOT NULL, verified_city_session_id text,
  FOREIGN KEY (schedule_id, park_id, service_date) REFERENCES scenic_schedules(schedule_id, park_id, service_date),
  UNIQUE(schedule_id, natural_key)
);
CREATE INDEX scenic_performance_date ON scenic_performances(park_id, service_date, program_name, start_time);
CREATE TABLE scenic_schedule_changes (
  change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schedule_id bigint NOT NULL REFERENCES scenic_schedules(schedule_id) ON DELETE CASCADE,
  park_id text NOT NULL, service_date date NOT NULL,
  program_name text NOT NULL, venue_name text NOT NULL,
  change_type text NOT NULL CHECK (change_type IN ('added','removed','time_changed','cancelled','restored')),
  old_start_time time, new_start_time time,
  review_status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scenic_change_date ON scenic_schedule_changes(park_id, service_date, change_id);
CREATE TABLE scenic_venue_bindings (
  binding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  park_id text NOT NULL REFERENCES scenic_parks(park_id), venue_name text NOT NULL,
  verified_poi_id text, routing_verified boolean NOT NULL DEFAULT false,
  verification_method text NOT NULL DEFAULT '', evidence_ref text NOT NULL DEFAULT '',
  reviewer text, reviewed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(park_id, venue_name),
  CHECK (verified_poi_id IS NOT NULL OR NOT routing_verified)
);
CREATE TABLE scenic_schedule_signals (
  signal_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  schedule_id bigint NOT NULL REFERENCES scenic_schedules(schedule_id),
  service_date date NOT NULL, signal_type text NOT NULL,
  affected_session_ids jsonb NOT NULL, review_status text NOT NULL,
  formal_itinerary_changed boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE scenic_admin_receipts (
  scope text NOT NULL, request_id text NOT NULL, request_sha256 text NOT NULL,
  response_json jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(scope, request_id)
);
INSERT INTO scenic_schedule_migrations(version) VALUES (1);
COMMIT;
