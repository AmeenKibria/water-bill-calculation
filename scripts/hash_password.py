#!/usr/bin/env python3
"""Generate SHA-256 hash for a password.

Usage:
    python scripts/hash_password.py <password>
    
Example:
    python scripts/hash_password.py mySecretPass123
    
Add the output to your Streamlit secrets:
    [passwords]
    ameen = "hash_here"
    jussi = "hash_here"
"""

import hashlib
import sys


def hash_password(password: str) -> str:
    """Hash password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/hash_password.py <password>")
        sys.exit(1)
    
    password = sys.argv[1]
    hashed = hash_password(password)
    print(f"Password: {password}")
    print(f"Hash:     {hashed}")
    print()
    print("Add to .streamlit/secrets.toml:")
    print(f'username = "{hashed}"')
