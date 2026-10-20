# Узел кластера: PostgreSQL 18 + Patroni.
# PG18 сменил PGDATA: VOLUME объявлен на /var/lib/postgresql, каталог данных по умолчанию
# /var/lib/postgresql/18/docker. Мы монтируем том ровно на /var/lib/postgresql и кладём
# данные в подкаталог data — иначе рецепты с ./data:/var/lib/postgresql/data молча создают
# анонимный том, а примонтированный каталог остаётся пустым.
FROM postgres:18-bookworm

# Версия Patroni закреплена осознанно, см. docs/adr/0007-patroni-version.md:
# Patroni >= 3.0.4 (проверено) в /cluster показывает у реплик state="streaming", а чекер ДЗ ждёт "running".
ARG PATRONI_VERSION=3.0.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv curl gettext-base iputils-ping \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/patroni \
 && /opt/patroni/bin/pip install --no-cache-dir "patroni[etcd3]==${PATRONI_VERSION}" psycopg2-binary \
 && /opt/patroni/bin/pip install --no-cache-dir "ydiff==1.2"   # patronictl из Patroni 3.0.0 не импортируется с ydiff >= 1.3

COPY patroni.yml.tpl /etc/patroni.yml.tpl
COPY entrypoint.sh post_bootstrap.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/post_bootstrap.sh \
 && chmod 644 /etc/patroni.yml.tpl

# Patroni отказывается работать от root
USER postgres
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
