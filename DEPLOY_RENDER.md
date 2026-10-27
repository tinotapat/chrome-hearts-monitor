# Chrome Hearts monitor on Render

This is a continuously running Python background worker. It needs no public
website, port, database, or Mac left running. Deploy the product monitor only;
the repository's appointment monitors are separate services and are not part
of this setup.

## Configuration and cost

Use `render.yaml` from this branch. It defines one Oregon worker on Render's
`0.5c-512mb` plan ($7/month) with a 1 GB persistent disk ($0.25/month), about
$7.25/month before taxes or extra usage. Use a free Hobby workspace where
available. Confirm the price in Render before creating the paid service.

- Build: `pip install -r requirements.lock`
- Start: `python -u cloud_worker.py`
- Python: 3.12.14 (same version used for local tests)
- Persistent mount: `/data`
- Product history: `/data/seen_products.json`
- Single worker, manual code deployments; no overlapping workers or cron jobs.
- `CH_POLL_SECONDS=30` is a target between sweep starts, not a guaranteed
  detection time. Each sweep visits dozens of pages sequentially. Slow responses
  can make sweeps substantially longer. A failed sweep backs off; 403/429 stops
  the crawl immediately and respects a longer Retry-After value.

## Initial deployment

1. Sign in to Render, connect the GitHub repository, and create a Blueprint
   using the deployment branch containing `render.yaml`.
2. At the secret prompts, enter `DISCORD_WEBHOOK_URL` directly into Render,
   and set `CH_RUN_MODE` to `baseline`. Never paste the webhook into chat,
   source code, a PR, a command line, or a log. Use a normal Discord text-channel
   webhook beginning with `https://discord.com/api/webhooks/`.
3. Review the worker and disk price, then deploy. A baseline is generated at
   runtime, where the persistent disk is mounted. Build and pre-deploy steps
   cannot access the disk, so do not seed there or in a one-off job.
4. Wait for `BASELINE READY: ... products saved; zero product alerts sent.`
   Both initial crawls must be complete and nonempty, including a successful
   sitemap refresh. Products seen in either crawl are remembered. Review the
   count and crawl coverage. The worker then waits without further polling.
5. Use the service's runtime Shell/SSH, if available, for a notification-free
   validation: `python chrome_hearts_monitor.py --once --dry-run`. This must
   leave history unchanged. To test Discord, run
   `python cloud_worker.py --test-webhook`; it sends one clearly labeled test.
6. Set `CH_RUN_MODE=monitor` in Render and save/redeploy. Expect
   `Loaded persistent history: ... products.` followed by an online Discord
   message. Inspect at least two successful sweeps and verify delivery in the
   intended channel. A healthy sweep should usually report `0 new` unless a
   product actually appeared after the baseline.
7. Restart/redeploy once. Confirm the same history was loaded and no old
   products are announced again. Verify disk mounting and state persistence
   in Render, not only on the local machine.

## Safeguards and limits

Baseline mode never overwrites an existing state file. Monitor mode refuses
to silently seed missing/empty/corrupt state. A persistent initialization marker
also prevents baseline mode from reseeding a deleted history file. Disk loss
with a completely new volume still requires operator judgment; keep Render
disk snapshots/backups and do not delete the disk during redeployment.

History accumulates product IDs, so products disappearing, restocking, and
reappearing do not trigger new-product alerts. An empty crawl does not erase
history. A failed Discord delivery leaves new IDs pending for a retry. A
partial batch or an ambiguous timeout can cause a repeated message; Discord
webhooks cannot provide exactly-once delivery.

The initial baseline covers products visible through the known categories,
linked categories, and sitemap. No scraper can prove that an item was never
sold before if it was absent from every initial page. Fine-jewelry categories,
unlinked items, pagination, or future markup changes may reduce coverage.
This is a new-product monitor, not a guaranteed stock/size-restock alert system.

Chrome Hearts' published terms restrict automated monitoring; the operator
confirmed permission for this deployment. Its robots file excludes raw
Demandware routes, including the upstream monitor's Search-Show category
fallback. Ensure the permission covers that route and the selected request
rate. Do not bypass access challenges or rotate proxies if requests are denied.

The webhook is read only from its environment variable. Discord requests
disable redirects, wait for server confirmation, suppress accidental mentions,
and omit secret-bearing exception chains and response bodies from errors.
Keep Render environment values hidden when sharing screenshots.

## Validation

Run `python -m unittest -v` in the repository. Tests cover silent initial
baselines, restart history, old-product reappearance, new-product persistence,
failed delivery, partial/empty baselines, sitemap errors, corrupt/missing state,
disk mount validation, throttling, and secret redaction. Live storefront checks
must be repeated on Render because cloud IP behavior can differ from a Mac.

## Sources

- [Render background workers](https://render.com/docs/background-workers)
- [Render pricing](https://render.com/pricing)
- [Persistent disk rules](https://render.com/docs/disks)
- [Blueprint specification](https://render.com/docs/blueprint-spec)
- [Environment variables and secrets](https://render.com/docs/configure-environment-variables)
- [Discord webhook delivery confirmation](https://docs.discord.com/developers/resources/webhook#execute-webhook)
- [Chrome Hearts terms](https://terms.chromehearts.com/terms-of-use)
- [Chrome Hearts robots file](https://www.chromehearts.com/robots.txt)
