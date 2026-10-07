# LeadForge


## Postgres for development and tests

SQLite is the zero-setup default. To run against PostgreSQL 16 (and to run the
Postgres leg of the persistence test, which is a required gate and fails rather than
skips when no server is available):

```
docker compose up -d postgres
export LEADFORGE_TEST_POSTGRES_URL=postgresql://leadforge:leadforge@localhost:5432/leadforge
uv run pytest
```

With the variable unset, the test starts a throwaway local cluster from installed
PostgreSQL server binaries instead. Set `DATABASE_URL` to the same URL to run the app on it.
