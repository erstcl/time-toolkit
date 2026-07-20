# Security policy

## Supported versions

Security fixes are made for the latest release. The project is pre-1.0, so users
should pin a release and review `CHANGELOG.md` before updating.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for this repository. Do not open a
public issue when a report contains an unpatched vulnerability, a real server URL,
a token, a cookie, a message, a username, a channel name or another person's data.

Include only the minimum synthetic reproduction:

- affected Time Toolkit version and Python version;
- operating system;
- affected interface: CLI, MCP, Python, HTTP or WebSocket;
- expected and actual behavior;
- synthetic steps or a minimal test using `.test` domains;
- whether credentials or message contents may have been exposed.

Never send a real `MMAUTHTOKEN`, Bearer token, CSRF token or HTTP service key. If a
credential may already have been exposed, revoke it at the Time server before
reporting the bug.

## Security boundaries

Time Toolkit is a local client, not a sandbox. Code running as the same operating
system user may be able to access the same Keychain, environment variables and
local processes. Profile write policies reduce accidental writes but do not defend
against hostile local code. See `docs/security.md` for the complete model.
