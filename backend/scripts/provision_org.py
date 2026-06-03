"""Thin wrapper so backend/scripts/provision_org.py is discoverable alongside
other scripts in this directory. Delegates to the importable module.

Usage:
    uv run python scripts/provision_org.py --org-name "Hogar" --org-type personal \\
        --email owner@example.com --password "s3cr3t" --party-name "Owner"
"""
from ibkr_control.scripts.provision_org import main

main()
