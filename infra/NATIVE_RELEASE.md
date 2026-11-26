# Native Jellyfin release preparation

Status: **ordinary plugin passed disposable integration; production activation
remains blocked pending coordinated release and real authorized playback**.
Candidate source 7fafd4cb4bb7cef4017b38f2ee055baa6616ea3f passed all nine
integration gates (run 45784145834), including all three private gates on
the same regular DLL (no lab symbol). Build 45784145991 has the same
regular plugin SHA-256. Never install a fixture build.

## Exact candidate artifacts

The integration workflow preserves the regular DLL **before** the separate
fixture rebuild and the three patched upstream assemblies from the exact
tested runtime. Publication requires all gates to pass. The generated
manifest pins source/backend revision, official upstream 12.4 commit,
original/observed component digests, official FFmpeg archive and exact
ffmpeg/ffprobe digests, and CI run. It is a candidate manifest only: it
cannot approve its own deployment. Verify the actual artifact against the
independent CI evidence and a reviewed release pin.

The read-only preflight now includes MediaBrowser.Controller.dll, needed
for exact finite HLS tail seeks. Its expected original hash comes from the
official disposable runtime, not a claim that it has already been verified
on the VPS. Ferrari must confirm all four installed files and mappings.
The previous three-component baseline remains historical.

## Transaction boundary

The existing upgrade_jellyfin_multimedia.py remains unchanged. The
native_release_transaction.py module covers only four known Jellyfin DLL
locations. It has no production service adapter, no CLI and no production
approval. A future reviewed source change must pin the exact regular
release hashes and the compatible backend revision.

Before activation a trusted Ferrari adapter must:

- verify exact live Jellyfin version/assembly identities, current hashes,
  source remote and revision, clean checkout, service isolation, free space,
  protected service health, existing admin fingerprint and no active playback
  jobs; stage in a separate private directory;
- verify the same-run regular artifact, approved source and backend, official
  FFmpeg archive and per-binary digests, dependencies and private executable
  path; do not replace any system-wide FFmpeg;
- separately stage backend runtime, configuration and private key provisioning
  with a verified compatible rollback, preserving the existing backend socket
  and secrets. No database migration is implied by the candidate manifest;
  review compatibility of schema and dependency lock separately;
- coordinate backend and Jellyfin in one guarded release with durable
  rollback of both components, configuration and FFmpeg selection. Check
  authenticated playback, fail-closed access, admin and protected services.
  Actual authorized media and client compatibility remain separate acceptance
  criteria; do not describe the synthetic run as their certification.

The DLL transaction verifies old/new hashes, writes private backups and a
durable journal before stopping the service, atomically replaces all four
files preserving ownership and permissions, then checks health and hashes.
Ordinary failures trigger rollback. Abrupt termination leaves a journal for
explicit recovery. No resident coordinator is introduced. The adapter must
hold exclusive control of the trusted directories and services, sufficient
disk space, no other deployment, and verified stop completion before writes.
The unit tests use temporary directories and simulated service controls.

No production approval is added here. No service, configuration, database,
private media or actual production binary is changed by this preparation.

## Isolated staging (not activation)

`native_release_stage.py` pins the exact nine-gate candidate 0994990c and
stages backend, official portable FFmpeg and the CI bundle as separate
bounded phases under the project's own native-release-stage directory.
Each verifies production baseline and service PIDs before and after.
Backend checks the existing dependency pins and migration tree with a
synthetic in-memory database and denied requests under vivoapksvc. FFmpeg
checks the official archive and both executable hashes and decodes synthetic
H264/AAC as jellyfinpink. The bundle phase requires an already authenticated
GitHub CLI; if unavailable, it reports a blocker rather than publishing
signed URLs or installing another transport. No service restart, actual
subscriber data, environment file modification or production approval.

## Coordinated transaction

The coordinator uses a single durable journal and lock for four DLLs, plugin
opt-in XML, two systemd drop-ins and two newly provisioned private key files.
No production service adapter is approved by this module. A future host
adapter must supply fixed destinations and pinned bytes, verify complete
Jellyfin/backend stops, reload unit definitions, start backend then Jellyfin,
and verify health and admin identity. On error it restores all nine files
and verifies old health. On explicit rollback after a running release, it
preserves legitimate plugin slot updates and restores only the opt-in flag.
An unrelated immutable file change or corrupt backup blocks recovery.
The existing checkout, staged FFmpeg runtime and original unit definitions
are not overwritten. No secret values are recorded in the journal or Git.

## Fixed host rehearsal

The host preparation uses the exact staged candidate and existing service
identities. The backend drop-in selects the root-controlled staged checkout
using an explicit uvicorn app-dir; the Jellyfin drop-in selects FFmpeg and
private playback without modifying the original launch script or unit
hardening. The key and env are newly provisioned host-only files. A real
VPS rehearsal copies the actual candidate and original files into a private
disposable directory, deliberately fails postflight, and checks all nine
file changes restore with simulated service controls. It additionally checks
access under the actual service user/group and confirms www-data cannot read
a synthetic key with the planned permissions. Real service activation still
requires a reviewed host service adapter and postflight/rollback approval.
