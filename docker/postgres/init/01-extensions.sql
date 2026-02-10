-- Initialize PostgreSQL extensions for Repotoire
-- This script runs automatically on first database creation

-- Enable UUID generation functions
-- Required for UUID primary keys (uuid_generate_v4())
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable cryptographic functions
-- Useful for hashing and encryption
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enable trigram matching for full-text search
-- Improves LIKE/ILIKE query performance
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Verify extensions are installed
DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL extensions initialized successfully';
    RAISE NOTICE 'Installed extensions:';
    RAISE NOTICE '  - uuid-ossp (UUID generation)';
    RAISE NOTICE '  - pgcrypto (cryptographic functions)';
    RAISE NOTICE '  - pg_trgm (trigram matching)';
END $$;
