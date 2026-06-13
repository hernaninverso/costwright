# Releasing costwright

Releases publish to PyPI automatically via **Trusted Publishing** (OIDC) — no API token is
stored in the repo, in CI, or anywhere. `.github/workflows/release.yml` does it on a version tag.

## One-time setup (PyPI side, ~1 min) — do this once

The `costwright` project already exists on PyPI. Register this repo as a trusted publisher:

1. https://pypi.org/manage/project/costwright/settings/publishing/
2. **Add a new pending/trusted publisher → GitHub**:
   - **Owner:** `hernaninverso`
   - **Repository name:** `costwright`
   - **Workflow name:** `release.yml`
   - **Environment name:** *(leave blank)*
3. Save.

After this, the account-wide API token used for the first manual publish can be **revoked** —
Trusted Publishing replaces it. (Recommended: revoke it from
https://pypi.org/manage/account/token/ for least blast-radius.)

## Cut a release

```bash
# 1. bump the version
#    pyproject.toml:  version = "0.2.0"
#    src/costwright/__init__.py:  __version__ = "0.2.0"
# 2. commit, then tag + push the tag:
git commit -am "release 0.2.0"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

Pushing the `v*` tag triggers `release.yml`: it runs the test suite + the dogfood
(`costwright check src --fail-on reject`), builds the sdist + wheel, and publishes to PyPI
via OIDC. Watch it under the repo's **Actions** tab.

No tokens. No `twine`. No manual upload.
