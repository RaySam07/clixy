# Security

Clixy sees your screen and holds an API key, so security matters.

## What Clixy does with your data

- **Your API key** is stored in Windows Credential Manager, encrypted for your
  Windows account. It is only ever sent to the AI provider you chose.
- **Your voice** is turned into text on your PC by default (faster-whisper).
  Nothing you say leaves the machine unless you switch Voice to Cloud.
- **Your screen** is captured only while you hold the shortcut, and the
  screenshot plus your question go to your AI provider to answer you.
- **No telemetry, no accounts, no server of ours.** Clixy talks only to the
  providers you configure.
- **History** (short notes per app) stays in `~/.clixy/` and can be turned
  off or wiped in Settings > Privacy.

## Reporting a vulnerability

Please don't open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://github.com/RaySam07/clixy/security/advisories/new)
with steps to reproduce. You'll get a reply within a few days.

If that link doesn't work for you, open an issue titled "Security contact
needed", without any details, and we'll set up a private channel.
