# RELEASING — FleetX Toolkit

## One-time setup

1. **Repo**: push the `pkg/` contents (package + `.github/`) to a GitHub repo.
   The repo (or at least its Releases) must be **public** — clients download the
   exe with no token. Keep it a dedicated repo if the code itself must stay
   private is not possible on your plan; otherwise a public repo is fine since
   the exe is distributed to the whole team anyway.

2. **Secrets** (repo → Settings → Secrets and variables → Actions):

   | Secret | Required | What |
   |---|---|---|
   | `GIST_TOKEN` | yes | Classic PAT with **gist** scope only. Lets CI write `_latest_version` / `_download_url` / `_sha256` into the access Gist. Without it the release still builds — you edit the Gist by hand |
   | `SIGN_PFX_BASE64` | later | Code-signing cert: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))` in PowerShell, paste the output |
   | `SIGN_PFX_PASSWORD` | later | PFX password |

   Signing is skipped automatically while the two SIGN_* secrets are absent.
   When Fleetx gets a cert (company PFX or Azure Trusted Signing), add them —
   no workflow change needed. Until then, SmartScreen may warn on first run of
   each new exe; users click "More info → Run anyway" once.

## Per-release routine (the whole thing)

```
1. Edit fleetx_toolkit/config.py  →  APP_VERSION = "3.1"
2. git commit -am "v3.1"
3. git tag v3.1 && git push && git push origin v3.1
```

CI then: verifies tag == APP_VERSION → builds the exe → signs (if cert) →
computes SHA256 → publishes the GitHub Release → patches the Gist meta.

Every user sees "⬆ Update to v3.1" at their next login. One click, app
restarts on the new version. Nothing else to do.

## v3.2 features — operator notes

- **SMS Command tab** (SemySMS): each user pastes the SemySMS API token once in
  the tab; it is stored in Windows Credential Manager (`FleetX-Toolkit-SemySMS`),
  never on disk. The tab is access-gated like any other — grant "SMS Command"
  in the User Access tab. Per-row SIM is chosen by name from a fixed list of 6
  SIMs; the tool maps name → device id internally.
- **Sample Excel buttons** on SMS Command, Device Add, SIM Update, and
  Vehicle-Device Map generate a correctly-headered template (with in-cell
  dropdowns for SIM Name and Device Type). Headers are pinned to what the code
  reads, so a downloaded sample always imports cleanly.
- **Shared custom sensor types**: the SensorType tab's "+ Save to shared list"
  button appends to a `_sensor_types` array in the access Gist (admins with a
  GitHub token only). Every client picks it up at next login. Like the update
  meta keys, `_sensor_types` is a `_`-prefixed key so it is never treated as a
  user in the access matrix.

## Safety properties

- **Version gate**: a tag that doesn't match `APP_VERSION` fails the build —
  you can never ship an exe that reports the wrong version to the updater.
- **Rules preserved**: the Gist step is read-modify-write on the `_` meta keys
  only; the user/tab access matrix is never touched by CI.
- **Rollback**: tag the previous commit as e.g. v3.2 (a higher number is
  required — the updater only moves forward), or hand-edit the three Gist keys
  to point at the older Release asset.
- **Integrity**: clients verify the Gist-pinned SHA256 before swapping.
  Signing adds the OS-level trust layer on top once the cert exists.
