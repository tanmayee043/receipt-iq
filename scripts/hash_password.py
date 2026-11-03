"""Make the OWNER_PASSWORD_HASH value for the API's login.

Usage: python scripts/hash_password.py

Asks without showing what you type, so the password stays out of shell history. Put the
result in an environment variable. It isn't the password, but keep it out of the repo.
"""

import getpass
import sys

from receipt_iq.security import hash_password

MIN_LENGTH = 12


def main() -> None:
    password = getpass.getpass("Password: ")
    if len(password) < MIN_LENGTH:
        sys.exit(f"use at least {MIN_LENGTH} characters")
    if getpass.getpass("Again: ") != password:
        sys.exit("those didn't match")
    print(hash_password(password))


if __name__ == "__main__":
    main()
