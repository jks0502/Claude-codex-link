---
name: how-to-use-claude
description: Plan, configure, and verify personal VPS network access for official Claude clients on Windows and iPhone, with careful routing, failure checks, and rollback. Use when setting up or troubleshooting a user-authorized Claude connection; do not use to evade regional eligibility, identity checks, account enforcement, or service safeguards.
---

# How to use Claude through a personal VPS

Help the user set up or troubleshoot a reliable route from their own devices to official Claude clients through a VPS they control. Adapt to the current device, network, client versions, and existing configuration. Do not assume a server, proxy, app, or prior test is still in the same state.

## Boundaries

- Start by identifying whether the user means Claude web, the official desktop app, the official iOS app, or Claude Code. These clients can use different network paths; success in one does not prove the others work.
- Use only the user's own account, devices, server, and legitimate payment or identity details. Check that the VPS provider permits the intended personal proxy use.
- A stable egress IP can reduce network variability. It does not establish supported-region eligibility, guarantee account access, prevent enforcement, or bypass KYC, regional restrictions, or service safeguards. Do not recommend fake identity/location, purchased accounts, token sharing, account rotation, or quota evasion.
- Never place API keys, SSH keys, UUIDs, private node URIs, subscription URLs, QR images, or unredacted logs in a public repository or ordinary report.

## Workflow

1. **Establish the target and baseline.** Confirm the requested Claude client, device, operating system, local network, and whether the user wants app-only routing, a dedicated browser, or device-wide routing. Inspect current configuration read-only first. Record what exists and back up only files/settings that may change. Reuse a working server; do not reinstall over an existing service.
2. **Check the server before changing it.** Confirm provider permission, instance state, OS, SSH access, existing listeners/services, cloud firewall/security group, host firewall, and a recovery route. A small Debian VPS can handle personal forwarding; it is not a model-hosting machine. If changing SSH or firewall settings, retain the current session and prove a second SSH login works before closing it.
3. **Deploy only what is needed.** For a new personal node, Xray with VLESS + REALITY is one possible design, not a universal requirement. Follow current upstream installation and protocol documentation; do not copy stale SNI, ports, fingerprints, or example credentials. Back up existing configuration, open only required inbound ports, and verify config syntax, service startup, persistence, and logs. Generate client materials locally and keep them private.
4. **Scope the device route.** On Windows, prefer an application-specific route or a dedicated browser profile when the user wants Claude traffic isolated. Preserve unrelated proxy, VPN, campus-network, and system settings. If using Clash Verge Rev/Mihomo, inspect its actual version and loaded configuration; add narrow Claude domains to an existing valid policy rather than replacing the profile with a generic sample. TUN captures traffic for rule evaluation; it does not mean every connection is proxied. For an iPhone setup, use the user's requested client (for example, Shadowrocket), back up its current config, and import the private node locally. Do not assume its menus or settings match a Mac or another app version.
5. **Check the actual paths.** Verify the public egress through the intended proxy, then verify the real Claude client can complete an ordinary request. Check DNS policy, IPv4/IPv6 behavior, browser-specific DNS/WebRTC paths, and whether a failed proxy request falls back to DIRECT. Test separately on each device and network the user intends to use. A listening port, ping, generic HTTP response, or IP reputation score is not end-to-end acceptance.
6. **Test failures and recovery.** With a harmless test request, check both an unreachable node while the proxy client is running and the proxy client/TUN being stopped. Record whether traffic blocks or falls back. Reboot and network-change checks are needed before claiming persistence or strong leak protection. Apply a firewall kill switch only when the user requested it, the exact app/network paths are understood, and a recovery path is ready.
7. **Troubleshoot by evidence.** Separate DNS, routing, TLS, provider firewall, client compatibility, service availability, region eligibility, and account issues. Preserve the exact error and timestamp. A 403 or a location prompt alone does not prove the account is banned; verify the observed egress and consult current official support guidance before account-level conclusions.
8. **Deliver and roll back.** Report confirmed, failed, and unverified checks separately, with client and network versions and sanitized evidence. List changed files/settings and their backup locations. Keep a short rollback path: exit Claude, restore only the changed configuration, remove only rules added for this setup, restore the previous proxy/network state, then verify ordinary connectivity. Never report setup as complete merely because the configuration was written.

## Acceptance checklist

- [ ] VPS permission, SSH recovery, firewall, service status, and restart behavior verified.
- [ ] Each requested device reaches Claude through the expected egress.
- [ ] DNS and IPv6 behavior are understood for the tested path.
- [ ] Proxy failure behavior is tested; direct fallback is either prevented or clearly disclosed.
- [ ] Ordinary browsing and required local/campus services still work.
- [ ] Private client materials are stored locally and excluded from public files.
- [ ] Backups, rollback steps, and remaining unverified items are recorded.

