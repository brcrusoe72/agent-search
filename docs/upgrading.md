# Upgrading an existing Docker installation

Older images ran as root. Their existing SQLite volumes may therefore be
root-owned. The repaired API runs as UID/GID 10001; image-build ownership
changes cannot change an already mounted volume.

Before starting the new image, stop both API services, retain their named
volumes, and run the ownership migration once for each existing data volume:

```bash
python scripts/migrate_data_ownership.py --volume EXISTING_DIRECT_DATA_VOLUME --volume EXISTING_PRIVATE_DATA_VOLUME
```

Find the exact existing names with `docker volume ls` and verify them with
`docker volume inspect`. The script refuses nonexistent names, changes only
ownership, and keeps all files. Its temporary root helper has a read-only root
filesystem, no network, and only the capabilities needed to traverse and chown
the mounted data. The application continues to run non-root. Take a normal
backup before upgrading an installation that contains valuable data.

The direct worker network changed from public `172.32.0.0/24` to private
`172.29.0.0/24`. Recreate the Compose networks using `docker compose down`
without `--volumes`, then start the repaired direct/private configuration.
Use the same Compose project name so the original named volumes are retained.
Do not use `down --volumes`: that would delete the data this migration preserves.

If rollback is needed, restore the previous images and Compose files with the
same project name and retained volumes. A previous root-run image can still
access data now owned by UID 10001. No package or image publication is required.

## SearXNG 2026.9.5 configuration

An image update does not update ignored runtime files. `prepare-searxng.sh`
deliberately leaves existing `searxng/settings.yml` and `settings.tor.yml` intact.
Back them up before upgrading, and compare them with the updated templates.
Preserve each instance's secret, custom settings, and the private outgoing Tor
proxy. For installations using the bundled defaults, regenerate from the new
templates and restore each original `server.secret_key` before restarting.

The new templates inherit the upstream Stack Overflow (`stackexchange`) and
Wikinews (`mediawiki`) implementations instead of overriding them with nonexistent
modules. Removed overrides for Reddit, Lobsters, and inactive Yahoo News do not
promise those engines are available. DuckDuckGo News and Reuters are enabled in
both configurations so the news strategy has the same declared sources.

Run `python scripts/check_searxng_config.py` to check the committed templates
against the pinned image. It loads both configurations in containers without
network access; it does not measure live search quality. After deploying,
inspect `/engines`, try both `mode=general` and `mode=news` on direct and private
routes, and retain upstream degradation diagnostics in the results.
