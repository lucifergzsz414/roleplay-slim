# Windows GUI troubleshooting

This guide applies to `roleplay-slim-windows-x64.zip` from GitHub Releases. Extract the entire archive before running either EXE.

## SmartScreen or an unknown-publisher warning

The current beta is not code-signed. Download it only from the project's [GitHub Releases](https://github.com/lucifergzsz414/roleplay-slim/releases), verify the ZIP against the accompanying `.sha256` file, and do not run third-party repackaged copies.

## The chat app cannot connect to `127.0.0.1`

1. Confirm that the launcher shows the green running state.
2. Copy the API URL exactly as shown; the default is `http://127.0.0.1:8795/v1`.
3. Open `http://127.0.0.1:8795/healthz`; a healthy proxy returns `{"status":"ok"}`.
4. If the check fails, close any other roleplay-slim windows and retry. Do not terminate unknown system processes.

## Port already in use or startup failure

Another launcher or proxy is usually still running. Close the old window first. Advanced users can inspect the port without changing anything:

```powershell
Get-NetTCPConnection -LocalPort 8795 -State Listen -ErrorAction SilentlyContinue
```

The current Windows GUI always uses `8795`; it cannot change ports in the UI. If another roleplay-slim window owns the port, close that window normally. If another application owns it, close or reconfigure the confirmed conflicting application, or use the Python proxy with a configurable port. Do not terminate an unknown process.

## HTTP 401 or 403

- `401` usually means the chat app did not send an API key or the key is invalid.
- `403` usually means the provider account, region, model, or key permissions reject the request.

The launcher never reads or stores the API key. Fix authentication in the original chat app, and never paste a real key into an issue, screenshot, or log.

## HTTP 404 or model not found

Make sure the provider URL and model name belong to the same provider. When unsure, leave the launcher's model field blank so it preserves the model sent by the chat app.

## HTTP 429

The provider is rate-limiting the account or its quota is exhausted. Wait and check the provider console. roleplay-slim does not bypass provider limits.

## HTTP 502 `failed to connect to upstream`

The local proxy received the request but could not reach the provider. Check the provider status, confirm the upstream URL uses HTTPS, verify the current VPN/proxy path, and temporarily test the provider's original URL without roleplay-slim. If the direct request also fails, the problem is usually outside roleplay-slim.

## Savings show 0%

Short conversations and recent turns are intentionally preserved, so 0% can be correct. Savings appear after older turns enter the compressible window. The stable persona/system prefix also remains byte-for-byte unchanged by design.

## Processes after closing

Closing the launcher only stops the proxy started by that window; it does not take ownership of an older leftover process. If the port check still shows a listener, record its PID and end it only after Task Manager confirms that it is `roleplay-slim-proxy.exe`. If you cannot confirm it, keep the PID and error details for a bug report. Do not bulk-kill Python or system processes.

## Still stuck?

Use the [bug report form](https://github.com/lucifergzsz414/roleplay-slim/issues/new?template=01-bug-report.yml). Include the version, provider, status code, and minimal reproduction steps. Remove API keys, Authorization headers, full conversations, personas, private URLs, and account details first.
