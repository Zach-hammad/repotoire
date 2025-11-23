# TimescaleDB for Repotoire Metrics

This directory contains Docker Compose configuration for running TimescaleDB locally for development.

## Quick Start

```bash
# Start TimescaleDB
cd docker/timescaledb
docker compose up -d

# Verify it's running
docker compose ps

# Check logs
docker compose logs -f timescaledb

# Connect with psql
docker compose exec timescaledb psql -U repotoire -d repotoire_metrics
```

## What's Included

- **TimescaleDB**: PostgreSQL with TimescaleDB extension for time-series data
- **pgAdmin** (optional): Web-based database management tool

## Environment Variables

Create a `.env` file in this directory (or export these variables):

```bash
# Database password
TIMESCALE_PASSWORD=your-secure-password

# pgAdmin password (optional, for --profile tools)
PGADMIN_PASSWORD=admin
```

## Accessing the Database

### Connection Details

- **Host**: `localhost`
- **Port**: `5432`
- **Database**: `repotoire_metrics`
- **User**: `repotoire`
- **Password**: Set via `TIMESCALE_PASSWORD` (default: `repotoire-dev-password`)

### Connection String

```
postgresql://repotoire:your-password@localhost:5432/repotoire_metrics
```

### Environment Variable for Repotoire

```bash
export REPOTOIRE_TIMESCALE_URI="postgresql://repotoire:repotoire-dev-password@localhost:5432/repotoire_metrics"
```

## Using pgAdmin (Optional)

pgAdmin is included but disabled by default. To start it:

```bash
docker compose --profile tools up -d
```

Access pgAdmin at: http://localhost:8080

- **Email**: admin@repotoire.local
- **Password**: Set via `PGADMIN_PASSWORD` (default: `admin`)

### Add Server in pgAdmin

1. Right-click "Servers" → "Register" → "Server"
2. General tab:
   - Name: `Repotoire Metrics`
3. Connection tab:
   - Host: `timescaledb` (Docker network name)
   - Port: `5432`
   - Database: `repotoire_metrics`
   - Username: `repotoire`
   - Password: Your `TIMESCALE_PASSWORD`

## Verifying the Schema

```sql
-- Check if TimescaleDB extension is installed
SELECT * FROM pg_extension WHERE extname = 'timescaledb';

-- Verify hypertable was created
SELECT * FROM timescaledb_information.hypertables;

-- Check continuous aggregates
SELECT * FROM timescaledb_information.continuous_aggregates;

-- Query sample data (after running some analyses)
SELECT * FROM code_health_metrics ORDER BY time DESC LIMIT 10;

-- View daily summary
SELECT * FROM daily_health_summary ORDER BY day DESC LIMIT 7;
```

## Stopping and Cleaning Up

```bash
# Stop containers (keeps data)
docker compose down

# Stop and remove all data
docker compose down -v

# Remove images
docker compose down --rmi all
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs timescaledb

# Common issues:
# - Port 5432 already in use (change port in docker-compose.yml)
# - Insufficient disk space
# - Permission issues with volumes
```

### Can't connect from Repotoire

```bash
# Test connection
psql postgresql://repotoire:your-password@localhost:5432/repotoire_metrics

# If that fails, check if container is running
docker compose ps

# Check if port is accessible
nc -zv localhost 5432
```

### Schema not initialized

The schema is automatically loaded on first container startup. If it fails:

```bash
# Manually run the schema
docker compose exec -T timescaledb psql -U repotoire -d repotoire_metrics < ../../repotoire/historical/schema.sql
```

## Production Deployment

For production, consider:

1. **Managed TimescaleDB**: Use Timescale Cloud or AWS RDS with TimescaleDB
2. **Secrets Management**: Use proper secret management (AWS Secrets Manager, Vault)
3. **Backups**: Configure automated backups
4. **Monitoring**: Set up Prometheus + Grafana for database monitoring
5. **SSL**: Enable SSL connections
6. **Resource Limits**: Tune PostgreSQL parameters for your workload

Example production connection string:

```
postgresql://user:pass@timescale.example.com:5432/repotoire?sslmode=require
```
