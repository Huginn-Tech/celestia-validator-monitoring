# Panels

The dashboard **Taut: Celestia validator** has 10 panels in 4 groups. The playbook builds it from your inventory: the Remote signer group appears only with `taut_signer: remote`, the Fibre group only with `taut_fibre_enabled: true`. Dashed red lines sit at the alert thresholds in [alerts.md](alerts.md).

## Validator

- **Node at chain head**: Is the node following new blocks, or stuck, syncing, or watching a halted chain, and is it peered? Three tiles: new blocks in the last 3 minutes (red at 0, which is what NodeNotAtHead alerts on), block sync (orange "yes" while the node catches up) and peers (red at 0).
- **Blocks since my last signature**: Is my validator signing right now? Near 0 while it signs; a line that climbs means blocks pass without its signature, and the dashed line is where ValidatorNotSigning fires. It works after restarts and jails, when missed-block counters freeze; right after a node start it shows the full block height until the first own signature.

## Remote signer

- **Signer latency vs 10 ms**: Am I within Celestia's 10 ms median requirement, and is consensus or Fibre (`raw_bytes`) signing getting slower? The median of the last 50 signatures must stay under the dashed 10 ms line; the p99 lines per message type show which kind of signature slows down. The median appears after 50 signatures since the node started and stays frozen while none succeed.
- **Signing failures by reason**: Which signing path fails? Empty on a healthy setup. `no_connection` or `timeout`: the KMS host or the link; proposal only: the KMS cannot propose; `raw_bytes` only: tmkms too old for Fibre. Every retry counts as a failure, so a short outage shows as a burst.

## Fibre

Panel links open the official [Fibre](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre.json) and [Fibre runtime](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/observability/docker/grafana/dashboards/fibre-runtime.json) dashboards for per-RPC and Go runtime detail, and [Tensile](https://tensile.huginn.tech) and [Valopers](https://testnet.celestia.valopers.com/fibre/providers) for the view from outside.

- **Time since last successful upload**: Is Fibre serving, not just running? A sawtooth that drops back with each upload is healthy; a line that climbs past the dashed threshold (ten uploads at this server's 7-day average rate, never under 1 hour) is FibreNotServing. It counts from Fibre's start until the first upload, and has no threshold line when the server had no upload in 7 days.
- **Shard requests**: Are uploads and downloads arriving and succeeding, is the storage limiter refusing uploads, and are uploads slow? Requests per second by `success`; rejected uploads by reason (the storage limiter refusing uploads); upload p95 latency on the right axis.
- **Fibre disk**: Will the Fibre disk hold the stake-based storage budget? Occupancy near the budget is normal at full load; worry when the free space is smaller than the budget minus occupancy (DiskLow). A budget of 0 means no limit: Fibre found no stake for the validator.

## Hosts

Panel links open [Node Exporter Full](https://grafana.com/grafana/dashboards/1860-node-exporter-full/) for everything else about a host.

- **Services per host**: Are celestia-appd, Fibre and tmkms running, and how often did systemd restart them? One tile per unit: green "active" or red "not active", and restarts in the last 24 hours (orange from 1). Only automatic restarts under `Restart=` count; a manual stop and start does not.
- **Free space per data disk**: Which data disk is close to full or read-only? One bar per configured mount: red below 3% free, orange below 10%, green above (the DiskLow thresholds); the read-only tile turns red on "yes".
- **Memory per host**: Is Fibre or the node running out of RAM? Available memory per host; OOM kills in the last hour on the right axis. Fibre can use up to about 27 GiB at its default connection limits ([connection caps and memory](https://github.com/celestiaorg/celestia-app/blob/v10.4.0/fibre/cmd/README.md#connection-caps-and-memory)); any OOM kill means the kernel killed a process, and `journalctl -k` names it.
