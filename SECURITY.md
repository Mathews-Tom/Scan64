# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.6.x | :white_check_mark: |
| < 0.6 | :x: |
| Main | :white_check_mark: |

## Reporting a Vulnerability

Do not disclose vulnerabilities, exploit details, or credentials in a public issue. Submit a private report through [GitHub Security Advisories](https://github.com/Mathews-Tom/Scan64/security/advisories/new).

Include the affected version, a minimal reproduction, impact, and any proposed mitigation. Maintainers will acknowledge the report and coordinate disclosure through the advisory.

## Desktop server boundary

The Tauri desktop server binds only to `127.0.0.1` and serves no web-client files.
The shell generates a fresh secret for each server launch and sends it in
`X-Scan64-Shell-Token`. Every actual request except `GET /health` requires an
exact, constant-time token match; health returns only `{"status":"ok"}`.
CORS preflights need no token, but only `tauri://localhost`,
`http://tauri.localhost` and `https://tauri.localhost` are allowed. There is no
wildcard origin. The separate player bearer-token ownership checks still apply.

The token prevents unauthenticated loopback requests, including requests from
unrelated browser pages. CORS is a browser restriction, not authentication or
an operating-system sandbox: another process that obtains the secret can use
the API, and software running as the same OS user may be able to inspect the
server environment or the app's memory. Do not log or disclose the shell token.
Browser/source and container servers do not install this desktop-only middleware.

The shell retains the server's stdin pipe. EOF requests graceful shutdown and
arms an independent three-second hard-exit deadline, including during startup
or a hung engine shutdown. On macOS/Linux the server owns and kills its isolated
process group, including Stockfish. On Windows it requires a kill-on-close Job
Object containing itself and its descendants; process exit closes the sole job
handle and kills the engines. Job setup failure stops startup. These mechanisms
do not depend on Stockfish cooperating with UCI shutdown. The five-second
shell-death requirement is subject to the three-OS lifecycle checks, not a claim
that the desktop shell is a security sandbox.
