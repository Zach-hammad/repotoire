#!/bin/sh
set -e

# Build REDIS_ARGS with authentication and persistence settings
# FALKOR_PASSWORD is set via Fly secrets

# Start with base args (dir is already set by run.sh, so just add persistence)
REDIS_ARGS="--appendonly yes --appendfsync everysec"

if [ -n "$FALKOR_PASSWORD" ]; then
    REDIS_ARGS="$REDIS_ARGS --requirepass $FALKOR_PASSWORD"
    echo "FalkorDB starting with password authentication enabled"
else
    echo "WARNING: No FALKOR_PASSWORD set, running without authentication"
fi

# Export for FalkorDB run.sh which uses this variable
export REDIS_ARGS

# Execute the original FalkorDB run.sh
exec /var/lib/falkordb/bin/run.sh
