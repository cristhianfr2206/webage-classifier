# WSL2 browser resource guidance

Docker Desktop and WSL2 share a Linux VM and kernel boundary. Container limits reduce accidental and hostile resource consumption but are not equivalent to a dedicated host or hardened multi-tenant sandbox. Keep Docker Desktop updated and treat its daemon as trusted.

The default browser worker uses one concurrent task, one browser context, one CPU, 768 MiB memory, 128 PIDs, 128 MiB shared memory, and bounded temporary filesystems. Increase limits only after measuring local workloads. Do not expose Chromium debugging ports or weaken its sandbox to work around local configuration.

If Chromium cannot start with its sandbox enabled, fix Docker Desktop/WSL2 user-namespace support or stop browser workers; do not add `--no-sandbox`.
