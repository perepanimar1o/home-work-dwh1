FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends pgbouncer postgresql-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# userlist и конфиги монтируются из репозитория (права 644: pgbouncer читает их не от root)
USER postgres
ENTRYPOINT ["pgbouncer"]
