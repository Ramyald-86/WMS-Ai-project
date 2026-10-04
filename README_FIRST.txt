FULFILLCORE - DEVELOPMENT HANDOFF

Purpose:
A compact technical handoff of the current FulfillCore project.

Included:
- application source code
- project config and Docker/Compose files
- .env/config files found in the project
- migrations and project documentation
- Git HEAD, history, status and uncommitted changes
- PostgreSQL schema only (no business/user rows)
- Docker/system metadata
- full inventory of files that exist in the project directory

Excluded to keep the archive compact:
- Docker container filesystem copies
- Docker volume contents
- full database data
- logs/cache/tmp
- virtual environments and node_modules
- uploads/media
- old backups and ZIP/TAR archives
- .git object database

See _TECHNICAL/ALL_SERVER_PROJECT_FILES.tsv for the complete file inventory.

SECURITY:
This is a PRIVATE archive. It may contain .env files, API keys,
database credentials and other secrets. Do not publish it.
