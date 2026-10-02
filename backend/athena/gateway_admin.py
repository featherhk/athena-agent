"""Local operator console: no network credit-grant endpoint exists."""
import argparse
import asyncio
from getpass import getpass
from pathlib import Path
from .auth import Auth
from .config import require_free_threading
from .credits import Credits,initialize_credits
from .store import Store


async def run(args):
    store=Store(args.data_dir.expanduser()/'accounts.sqlite3')
    # Gateway owns initialization/recovery. Admin must never interrupt its runs.
    if not store.path.exists():
        raise SystemExit('Start the gateway once before using this console.')
    try:
        credits=Credits(store)
        users=await store.rows('SELECT id,username FROM users WHERE username=?',(args.username,))
        if args.command=='create-user':
            if users:
                raise SystemExit('Account already exists; nothing changed.')
            password=getpass('New account password (hidden): ')
            if len(password)<12:
                raise SystemExit('Use at least 12 characters for a deployed account.')
            if password!=getpass('Repeat password (hidden): '):
                raise SystemExit('Passwords differ; nothing changed.')
            user,token=await Auth(store,bootstrap_owner=False).register(args.username,password)
            from .auth import token_hash
            await store.execute('DELETE FROM auth_sessions WHERE token_hash=?',(token_hash(token),))
            print('Account created with zero balance. No token retained.')
        elif args.command=='grant':
            if len(users)!=1:
                raise SystemExit('Create/register the account first.')
            await credits.grant(users[0]['id'],args.micros,args.operation_key,args.reason)
            print(await credits.balance(users[0]['id']))
        else:
            if not users:
                raise SystemExit('Account not found.')
            print(await credits.balance(users[0]['id']))
    finally:
        await store.close()


def main():
    parser=argparse.ArgumentParser(description='Athena hosted account operator console')
    parser.add_argument('--data-dir',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create-user');create.add_argument('username')
    grant=sub.add_parser('grant');grant.add_argument('username')
    grant.add_argument('--micros',required=True,type=int,help='USD microcredits, 10 USD = 10000000')
    grant.add_argument('--operation-key',required=True);grant.add_argument('--reason',required=True)
    status=sub.add_parser('status');status.add_argument('username')
    args=parser.parse_args();require_free_threading();asyncio.run(run(args))


if __name__=='__main__':
    main()
