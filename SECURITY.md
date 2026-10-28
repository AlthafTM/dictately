# Security

## Reporting a vulnerability

Use [GitHub's private reporting form](https://github.com/AlthafTM/dictately/security/advisories/new) if it is available. Include the affected version, platform, reproduction steps, and expected impact. Use synthetic audio and text; do not attach passwords, personal recordings, or private transcripts.

If private reporting is unavailable, open an issue asking for a private reporting channel without posting the vulnerability details. Please allow time for a fix before public disclosure.

Security fixes target the latest release and `main`. Update older installations before reporting a problem that may already be fixed.

## Security checks

- CodeQL configuration covers Rust, Python, C#, and GitHub Actions
- Dependency checks query OSV for all exact requirement pins and locked Rust crate versions; CI also checks the installed CPU/desktop Python dependencies
- Dependabot proposes weekly Python, Rust, and GitHub Actions updates
- Workflow actions are pinned to commit hashes; builds use read-only repository tokens, with release publication in a separate write-enabled job

Run `python scripts/security/audit_dependencies.py --installed` in the app environment to check its current dependency versions. Advisory checks need internet access and send package names and versions, not transcripts. An unavailable advisory service fails the check rather than reporting a pass.

Passing checks is not a security rating or a guarantee of no vulnerabilities. After publishing a workflow change, inspect [Actions](https://github.com/AlthafTM/dictately/actions) and the [Security tab](https://github.com/AlthafTM/dictately/security). Branch rules, private reporting, secret scanning, and push protection are repository settings; source configuration alone does not prove they are enabled.

## Local data and installation

Recognition runs locally after setup. Setup and model updates download dependencies and weights. Windows Turbo downloads use tracked SHA-256 hashes; optional models and the macOS/Linux preview use a resolved Hugging Face revision. Verify downloads from the official releases. Windows installers are currently unsigned.

History contains plaintext transcripts. On macOS/Linux, Dictately restricts its data directory to the current user and writes state files with owner-only permissions. Windows relies on the user's LocalAppData permissions. Protect your OS account and backups; this does not encrypt data or securely erase older copies. Destination apps and the desktop clipboard have their own storage and privacy behavior.
