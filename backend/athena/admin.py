"""Operator-only configuration. Never expose provider credentials to client APIs."""
import argparse
from contextlib import closing
from getpass import getpass
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from .config import Config, require_free_threading
from .profiles import Profile
from .store import now, SCHEMA_VERSION


def archive_events(config, vacuum=False):
    from .event_log import archive_legacy_batch
    from .lock import InstanceLock
    if config.data_dir.resolve().is_relative_to(config.core_dir.resolve()):
        raise SystemExit('Runtime data and backups must remain outside the application core.')
    # Use the same exclusive lock as the dispatcher, including during VACUUM.
    # An idle server can otherwise wake a scheduled job halfway through maintenance.
    with closing(InstanceLock(config.data_dir / 'instance.lock')):
        with closing(sqlite3.connect(config.data_dir / 'athena.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA busy_timeout=5000')
            if not 10 <= db.execute('PRAGMA user_version').fetchone()[0] <= SCHEMA_VERSION:
                raise SystemExit('Upgrade the application database before archiving events.')
            if db.execute("SELECT 1 FROM runs WHERE status IN ('running','queued') LIMIT 1").fetchone():
                raise SystemExit('Wait for active work to finish before archiving events.')
            folder = config.data_dir / 'backups'
            folder.mkdir(mode=0o700, exist_ok=True)
            backup = folder / ('before-event-archive-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
            backup.touch(mode=0o600, exist_ok=False)
            with closing(sqlite3.connect(backup)) as saved:
                db.backup(saved)
            totals = {'events': 0, 'before_bytes': 0, 'after_bytes': 0}
            while True:
                with db:
                    batch = archive_legacy_batch(db)
                for key, value in batch.items():
                    totals[key] += value
                if not batch['events']:
                    break
            if vacuum:
                db.execute('VACUUM')
            return {**totals, 'backup': str(backup), 'vacuumed': vacuum}


def main():
    parser = argparse.ArgumentParser(description="Athena operator configuration")
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("provider-key", help="Set a server-side credential")
    key.add_argument("--profile", default="default")
    key.add_argument("--file", type=Path, help="Read a credential file without echoing its contents")
    profile = commands.add_parser("register-model", help="Register an immutable Chat Completions profile")
    profile.add_argument("--id", required=True)
    profile.add_argument("--provider", required=True)
    profile.add_argument("--model", required=True)
    profile.add_argument("--base-model", required=True)
    profile.add_argument("--api-base", required=True)
    profile.add_argument("--instruction-role", choices=["system", "developer"], default="system")
    profile.add_argument("--output-parameter", choices=["max_tokens", "max_completion_tokens"], default="max_tokens")
    profile.add_argument("--max-output-tokens", type=int, default=16384)
    profile.add_argument("--context-window-tokens", type=int)
    profile.add_argument("--context-window-source")
    profile.add_argument("--context-chars", type=int, default=180000)
    profile.add_argument("--preserve-reasoning", action="store_true")
    profile.add_argument("--options", default="{}", help="JSON generation options; no credentials")
    commands.add_parser("status", help="Show configuration presence; never credentials")
    archive = commands.add_parser('archive-events', help='Offline, lossless stream archive with an automatic backup; stop Athena first')
    archive.add_argument('--vacuum', action='store_true', help='Also reclaim freed database pages')
    args = parser.parse_args()
    require_free_threading()
    config = Config.from_env()
    database = config.data_dir / "athena.sqlite3"
    if not database.exists():
        raise SystemExit("Start Athena once to initialize the application database.")
    if args.command == 'archive-events':
        print(json.dumps(archive_events(config, args.vacuum)))
        return
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        if args.command == "provider-key":
            if args.profile != "default" and not db.execute("SELECT 1 FROM model_profiles WHERE id=? AND enabled=1", (args.profile,)).fetchone():
                raise SystemExit("Register this model profile first.")
            value = args.file.expanduser().read_text().strip() if args.file else getpass("Service credential (hidden): ").strip()
            if len(value) < 8 or any(c.isspace() for c in value):
                raise SystemExit("Credential was not saved: invalid input.")
            name = "deepseek_api_key" if args.profile == "default" else f"provider_key:{args.profile}"
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name, value))
            print("Service credential saved. No restart required. Secret omitted.")
        elif args.command == "register-model":
            value = Profile(id=args.id, provider=args.provider, model=args.model, base_model=args.base_model,
                            api_base=args.api_base, instruction_role=args.instruction_role,
                            output_parameter=args.output_parameter, max_output_tokens=args.max_output_tokens,
                            context_chars=args.context_chars, context_window_tokens=args.context_window_tokens, context_window_source=args.context_window_source, preserve_reasoning=args.preserve_reasoning,
                            options=json.loads(args.options))
            try:
                db.execute("INSERT INTO model_profiles VALUES (?,?,1,?)", (value.id, json.dumps(value.snapshot()), now()))
            except sqlite3.IntegrityError:
                raise SystemExit("This profile id already exists. Use a new id so active requests and audit snapshots remain stable.")
            print("Model profile registered. Configure its credential with provider-key --profile <id>.")
        else:
            rows = db.execute("SELECT id,definition,enabled FROM model_profiles ORDER BY id").fetchall()
            for identifier, definition, enabled in rows:
                name = "deepseek_api_key" if identifier == "default" else f"provider_key:{identifier}"
                present = bool(db.execute("SELECT 1 FROM settings WHERE key=? AND length(value)>0", (name,)).fetchone())
                present |= identifier == "default" and bool(config.api_key)
                data = json.loads(definition)
                print(json.dumps({"profile": identifier, "model": data["model"], "enabled": bool(enabled), "credential_present": present}))


if __name__ == "__main__":
    main()
