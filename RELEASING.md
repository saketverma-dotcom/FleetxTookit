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
