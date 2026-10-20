scope: ${PATRONI_SCOPE}
name: ${NODE_NAME}

restapi:
  listen: 0.0.0.0:8008
  connect_address: ${NODE_NAME}:8008

etcd3:
  hosts: etcd1:2379,etcd2:2379,etcd3:2379

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    synchronous_mode: false
    postgresql:
      use_pg_rewind: true
      use_slots: true
      parameters:
        wal_level: logical                  # CDC (ДЗ №2): logical decoding
        hot_standby: "on"
        max_wal_senders: 10
        max_replication_slots: 10
        wal_log_hints: "on"                 # нужен pg_rewind
        max_connections: 100
        shared_buffers: 128MB
        timezone: UTC
        datestyle: "ISO, YMD"
        password_encryption: scram-sha-256
  initdb:
    - encoding: UTF8
    - data-checksums
  # стенд учебный: пароли простые, доступ из любой сети docker; в бою так нельзя
  pg_hba:
    - local all all trust
    - host all all 127.0.0.1/32 trust
    - host replication replicator 0.0.0.0/0 scram-sha-256
    - host all all 0.0.0.0/0 scram-sha-256
  post_bootstrap: /usr/local/bin/post_bootstrap.sh

postgresql:
  listen: 0.0.0.0:5432
  connect_address: ${NODE_NAME}:5432
  data_dir: /var/lib/postgresql/data
  bin_dir: /usr/lib/postgresql/18/bin
  pgpass: /tmp/pgpass
  basebackup:
    - checkpoint: fast           # без этого pg_basebackup ждёт растянутый checkpoint (~4.5 мин при checkpoint_timeout=5min)
    - max-rate: 100M
  authentication:
    superuser:
      username: postgres
      password: postgres
    replication:
      username: replicator
      password: replicator
  parameters:
    unix_socket_directories: /var/run/postgresql

tags:
  nofailover: false
  noloadbalance: false
  clonefrom: false
  nosync: false
