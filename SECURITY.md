# Security Policy

Thank you for helping keep The Architect and its users safe.

## Supported Versions

Only the latest minor release series receives security updates.

| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |
| < 1.0   | ❌        |

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

If you discover a security vulnerability in The Architect, report it privately
so a fix can be prepared before the issue is disclosed publicly.

### How to report

Email: **[inetanel@me.com](mailto:inetanel@me.com)**

Include (as much as you can provide):

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- The version of The Architect affected (`architect --version`)
- Your operating system and Python version
- Any proof-of-concept code or logs (redact secrets before sharing)

If you prefer encrypted communication, mention that in your first email and a
PGP key or Signal number will be provided.

### What to expect

- **Acknowledgement within 72 hours** — you will receive confirmation that the
  report was received.
- **Initial assessment within 7 days** — severity and potential fix timeline.
- **Coordinated disclosure** — we will work with you on a disclosure timeline.
  Credit will be given in the release notes unless you prefer to remain
  anonymous.

## What counts as a security issue

The Architect is a local-first CLI tool that orchestrates AI coding agents. It
has no network server component, no authentication layer, and no persistent
service. Security issues are typically things like:

- Arbitrary command execution via untrusted input (goal strings, context files,
  task file contents)
- Path traversal or unintended writes outside the project directory
- Leaking API keys or `.architect/logs/` contents to unauthorised destinations
- Compromise of the PyPI release pipeline or release signatures
- Malicious prompt injection that causes The Architect to bypass its own
  safety guards (e.g. running a task the user explicitly cancelled)

Bugs that require an attacker to already have local code execution on the
user's machine are generally **not** considered security issues.

## Out of scope

- Issues in the underlying AI providers (OpenCode, Claude Code, OpenRouter) —
  please report those to the respective projects.
- Issues in third-party Python dependencies — please report those upstream
  (Dependabot also monitors these automatically).
- Social engineering, physical attacks, or attacks requiring already-compromised
  developer machines.

## Safe harbor

Good-faith security research that follows this policy will not result in
legal action from the maintainer. Please avoid:

- Accessing data that is not your own
- Disrupting other users
- Public disclosure before a fix has been released (or 90 days have passed,
  whichever comes first)

Thank you for making The Architect safer for everyone.
