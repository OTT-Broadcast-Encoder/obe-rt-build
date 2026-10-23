#!/usr/bin/env python3
"""Create the optional webpanel account without exposing a password in argv."""

import argparse
import getpass
import sys

from auth import AuthError, setup_auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-file", required=True, help="File privato di autenticazione del pannello")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-stdin", action="store_true", help="Leggi la password da stdin invece del terminale")
    parser.add_argument("--replace", action="store_true", help="Sostituisci l'account e revoca tutte le sessioni")
    args = parser.parse_args()
    try:
        if args.password_stdin:
            password = sys.stdin.read(1026)
            if password.endswith("\n"):
                password = password[:-1]
                if password.endswith("\r"):
                    password = password[:-1]
        else:
            if not sys.stdin.isatty():
                raise ValueError("Usa --password-stdin per configurare l'accesso senza un terminale interattivo.")
            password = getpass.getpass("Password (almeno 12 caratteri): ")
            if password != getpass.getpass("Ripeti password: "):
                raise ValueError("Le password non coincidono.")
        setup_auth(args.auth_file, args.username, password, overwrite=args.replace)
    except (AuthError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Configurazione annullata.", file=sys.stderr)
        return 1
    print("Autenticazione configurata. Le sessioni precedenti sono revocate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
