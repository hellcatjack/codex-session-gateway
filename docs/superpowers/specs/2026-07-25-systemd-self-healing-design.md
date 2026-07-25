# Systemd Network Readiness and Gateway Self-Healing Design

## Context

On 2026-07-10 the gateway started before DNS was available. All four Telegram
adapter threads failed during initialization, but their exceptions did not reach
the main thread. After joining the terminated threads, the main process returned
with exit status 0. Because the systemd unit used `Restart=on-failure`, systemd
considered the stop successful and left the enabled service inactive.

The host already enables both wait-online services. During the affected boot,
`network-online.target` was reached about 23 seconds after the gateway had
started, so ordering the gateway after that target addresses the observed boot
race.

## Goals

- Start the gateway only after `network-online.target` is reached.
- Restart the entire gateway when any bot adapter thread exits, whether by
  exception or by an unexpected normal return.
- Restart the gateway whenever its main process exits.
- Preserve the multi-bot architecture and five-second restart delay.
- Load the newly configured `trader` bot alongside the existing four bots.
- Add automated regression coverage for thread supervision and unit-file policy.

## Non-Goals

- Splitting bots into separate systemd services.
- Retrying Telegram initialization inside each adapter.
- Changing Telegram message routing, session storage, or Codex execution.
- Cleaning stale database state or rotating credentials.

## Selected Design

### Systemd Startup and Restart Policy

The repository unit and installed unit will declare:

```ini
[Unit]
Wants=network-online.target
After=network-online.target

[Service]
Restart=always
RestartSec=5
```

`Wants` pulls the online target into the transaction, while `After` prevents the
gateway from starting before it completes. `Restart=always` covers both non-zero
and zero exits. The existing five-second delay prevents a tight restart loop and
stays below systemd's default start-rate threshold.

### Bot Thread Supervision

Each adapter continues to run in its existing daemon thread. A small wrapper
around `adapter.run` reports the first thread termination to the main thread
through a thread-safe queue. The report contains the bot name and either the
raised exception or an explicit marker for an unexpected normal return.

The main thread waits on that queue instead of joining every thread in sequence.
When any adapter terminates, the main thread raises `RuntimeError`, chaining the
original exception when one exists. The module-level exception handler logs the
failure and re-raises it, producing a non-zero process exit. Remaining daemon
threads end with the process, and systemd starts a clean gateway instance.

This design detects a failed bot immediately and avoids the current failure mode
where the main thread can block forever while one bot is already dead.

### Trader Bot Configuration

The newly added trader credentials currently exist only in `.env` and use mixed
case suffixes that cannot be referenced by the configuration loader's uppercase
environment-variable pattern. The local variables will be normalized without
changing their values:

- `TELEGRAM_BOT_TOKEN_TRADER`
- `CODEX_CLI_RESUME_ID_TRADER`
- `CODEX_WORKDIR_TRADER`

The ignored local `config.toml` will gain a `[[bots]]` entry named `trader` that
uses those environment references and the existing authorized Telegram user.
The tracked `.env.example` and `config.toml.example` files will receive matching
placeholder entries so the deployed configuration is reproducible without
committing secrets.

## Error Handling and Observability

- Adapter exceptions retain their traceback in the bot thread.
- The main process emits a concise error identifying the terminated bot.
- An adapter that returns normally is still treated as unexpected termination.
- Systemd records a failed process result and increments the restart count.
- Existing journal rate limiting and Telegram log redaction remain unchanged.

## Testing

Automated tests will verify:

1. An adapter exception causes the supervisor to raise and identify the bot.
2. An adapter's normal return is also treated as a gateway failure.
3. Existing per-bot session-manager isolation remains intact.
4. The deploy unit contains `Wants=network-online.target`,
   `After=network-online.target`, `Restart=always`, and `RestartSec=5`.
5. Configuration loading accepts the five-bot setup and resolves the uppercase
   trader environment references.
6. The full test suite remains green.

## Deployment Verification

1. Copy the tested unit into `/etc/systemd/system` and run `systemctl daemon-reload`.
2. Start the gateway and verify all five Telegram applications reach their
   started state without startup errors.
3. Record the main PID, terminate that process once, and verify systemd replaces
   it with a new PID after the configured delay.
4. Confirm the restarted service is active, all five bot polling connections are
   present, and the journal contains no unhandled startup exception.

The runtime termination test is intentionally limited to the gateway process;
systemd performs the recovery and no persistent data is deleted.

## Rollback

Restore the previous unit and `src/main.py`, remove the local `trader` bot entry
if needed, run `systemctl daemon-reload`, and restart the service. No database
migration is part of this design, so rollback has no data dependency.
