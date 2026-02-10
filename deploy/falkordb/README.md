# FalkorDB Deployment on Fly.io

This directory contains the deployment configuration for running FalkorDB on Fly.io with persistent storage and password authentication.

## Overview

- **App Name**: `repotoire-falkor`
- **Region**: `iad` (US East)
- **FalkorDB Version**: 4.14.8 (latest)
- **Redis Version**: 8.2.2
- **Storage**: 10GB encrypted volume
- **Memory**: 1GB

## Prerequisites

1. [Fly CLI](https://fly.io/docs/hands-on/install-flyctl/) installed
2. Authenticated with Fly.io: `fly auth login`

## Initial Deployment

The app has already been deployed. If you need to redeploy or set up a new instance:

```bash
# 1. Create the Fly app
fly apps create repotoire-falkor

# 2. Create persistent volume (10GB in iad region)
fly volumes create falkordb_data --region iad --size 10 -a repotoire-falkor

# 3. Generate and set password secret
FALKOR_PWD=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)
echo "Save this password: $FALKOR_PWD"
fly secrets set FALKOR_PASSWORD="$FALKOR_PWD" -a repotoire-falkor

# 4. Allocate IP addresses
fly ips allocate-v4 --shared -a repotoire-falkor
fly ips allocate-v6 -a repotoire-falkor

# 5. Deploy
cd deploy/falkordb
fly deploy --local-only
```

## Connecting to FalkorDB

### Via Fly Proxy (Development)

For local development, use `fly proxy` to create a secure tunnel:

```bash
# Start proxy (runs in foreground)
fly proxy 16379:6379 -a repotoire-falkor

# In another terminal, connect with redis-cli
redis-cli -h 127.0.0.1 -p 16379 -a "$FALKOR_PASSWORD"

# Or with Python
import redis
r = redis.Redis(host='127.0.0.1', port=16379, password=FALKOR_PASSWORD)
```

### Via Private Network (Production)

From other Fly apps in the same organization, connect via the internal network:

```python
import redis

r = redis.Redis(
    host='repotoire-falkor.internal',
    port=6379,
    password=os.environ['FALKOR_PASSWORD']
)
```

### Via SSH Console

```bash
fly ssh console -a repotoire-falkor -C "redis-cli -a '\$FALKOR_PASSWORD' PING"
```

## FalkorDB Graph Commands

```bash
# Create a graph
GRAPH.QUERY my_graph "CREATE (:User {name: 'Alice'})-[:KNOWS]->(:User {name: 'Bob'})"

# Query the graph
GRAPH.QUERY my_graph "MATCH (u:User) RETURN u.name"

# List all graphs
KEYS *

# Delete a graph
GRAPH.DELETE my_graph
```

## Multi-Tenant Usage

Create isolated graphs using organization slug prefixes:

```bash
# For organization "acme"
GRAPH.QUERY acme_codebase "CREATE (:File {path: 'src/main.py'})"

# For organization "globex"
GRAPH.QUERY globex_codebase "CREATE (:File {path: 'src/app.py'})"
```

## Operations

### Check Status

```bash
fly status -a repotoire-falkor
```

### View Logs

```bash
fly logs -a repotoire-falkor
```

### Restart

```bash
fly machine restart <machine-id> -a repotoire-falkor
# Or restart all machines
fly apps restart repotoire-falkor
```

### SSH Console

```bash
fly ssh console -a repotoire-falkor
```

### Scale Memory/CPU

Edit `fly.toml` and redeploy:

```toml
[[vm]]
  memory = '2gb'  # Increase memory
  cpus = 2        # Add more CPUs
```

### Resize Volume

```bash
fly volumes extend <volume-id> --size 20 -a repotoire-falkor
```

## Configuration

### Environment Variables

| Variable | Description | Set Via |
|----------|-------------|---------|
| `FALKOR_PASSWORD` | Redis authentication password | `fly secrets` |
| `FALKORDB_ARGS` | FalkorDB module configuration | `fly.toml [env]` |

### Persistence

- **RDB Snapshots**: Enabled by default
- **AOF (Append Only File)**: Enabled via `--appendonly yes`
- **Sync Frequency**: Every second (`--appendfsync everysec`)

Data is stored in `/var/lib/falkordb/data/` which is mounted to the Fly volume.

## Troubleshooting

### Connection Refused

1. Check the machine is running: `fly status -a repotoire-falkor`
2. Verify health checks: `fly machine status <machine-id> -a repotoire-falkor`
3. Check logs for errors: `fly logs -a repotoire-falkor`

### Authentication Errors

1. Verify password is set: `fly secrets list -a repotoire-falkor`
2. Reset if needed: `fly secrets set FALKOR_PASSWORD=<new-password> -a repotoire-falkor`

### Data Not Persisting

1. Check volume is mounted: `fly ssh console -a repotoire-falkor -C "df -h"`
2. Verify data directory: `fly ssh console -a repotoire-falkor -C "ls -la /var/lib/falkordb/data/"`

## Security Considerations

- Password is stored as a Fly secret (not in config files)
- Volume is encrypted at rest
- Internal network access recommended for production
- Consider setting up WireGuard VPN for secure external access

## Cost Estimate

| Resource | Specification | Monthly Cost |
|----------|---------------|--------------|
| VM | 1 shared CPU, 1GB RAM | ~$5 |
| Volume | 10GB SSD | ~$1.50 |
| IP | Shared IPv4 | Free |
| **Total** | | **~$6.50/month** |

## Files

- `fly.toml` - Fly.io app configuration
- `Dockerfile` - Container build with auth wrapper
- `start.sh` - Startup script for password configuration
