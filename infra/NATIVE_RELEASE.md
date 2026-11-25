# Native Jellyfin release preparation

Status: **blocked for production activation**. The integrated synthetic suite
at 170e23b9d78453040498e23db2070f34dfcc166c passed eight gates
(run 45695767667), but the native private playback wiring is still lab-only.
Do not install the lab plugin or enable the fixture environment switches.

## Transaction boundary

The existing upgrade_jellyfin_multimedia.py remains unchanged. The new
native_release_transaction.py is a reusable, non-executable file transaction
for the three known Jellyfin DLL locations. It has no production service
adapter, no CLI and no production approval. A future reviewed source change
must pin regular release hashes and the compatible backend revision.

Before activation a trusted Ferrari adapter must:

- verify the exact live Jellyfin version/assembly identities, current hashes,
  source remote and revision, clean checkout, service isolation, free space
  and protected service health, existing admin fingerprint and no active
  playback jobs; stage in a separate private directory;
- obtain reviewed regular (non-fixture) artifacts, prove actual native private
  playback on the regular build, and verify official FFmpeg provenance,
  dependencies and compatibility; the FFmpeg distribution is NOT in this
  three-DLL transaction;
- separately stage backend runtime, configuration and private key provisioning
  with a verified compatible rollback, preserving the existing backend socket
  and secrets. Do not restart protected services or modify service isolation;
- coordinate the backend and Jellyfin steps as a single guarded release, with
  explicit postflight for authenticated playback, fail-closed access, admin
  and protected services. If any step fails, restore compatible versions.

The DLL transaction verifies all old/new hashes, writes private backups and
a durable journal before stopping the service, atomically replaces each file
with preserved permissions/ownership, then checks health and hashes. Ordinary
failures trigger rollback. An abrupt termination leaves a journal for an
explicit guarded recovery invocation; no resident coordinator is introduced.
The callback adapter must ensure exclusive control of the trusted filesystem
directories and services, sufficient disk space, no other deployment, and
that stop has actually completed before touching files. The unit tests use
temporary directories and simulated service controls, not the VPS.

No configuration, database, real media, PINK service or actual production
component is touched by this preparation. Do not treat this transaction core
as a deployable full release.
