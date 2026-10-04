from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app/main.py").read_text(encoding="utf-8")
pool = (root / "app/services/db_pool_tuning.py").read_text(encoding="utf-8")
assert 'version="0.7.9.14"' in main
assert 'tune_db_pool(db)' in main
assert '/health/db-pool' in main
assert 'FC_DB_POOL_SIZE", 20' in pool
assert 'FC_DB_MAX_OVERFLOW", 20' in pool
assert 'FC_DB_POOL_TIMEOUT", 5' in pool
assert 'pool._pre_ping = True' in pool
print('OK: FulfillCore 0.7.9.14 DB pool reliability smoke-check passed')
