FROM falkordb/falkordb:v4.14.10

# FalkorDB deployment with password authentication for Fly.io
# Password is passed via FALKOR_PASSWORD environment secret

# Copy startup script that wraps the original run.sh
COPY deploy/falkordb/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 6379

# Override entrypoint to use our wrapper
ENTRYPOINT ["/start.sh"]
