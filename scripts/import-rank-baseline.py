#!/usr/bin/env python3
"""Run from repo root: PYTHONPATH=. python3 scripts/import-rank-baseline.py file.csv."""
import argparse
from backend.app.db import db_conn
from backend.app.services.rank_tracking import import_baseline

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import unverified legacy SerpApi rank history (idempotent).')
    parser.add_argument('csv_path')
    args = parser.parse_args()
    with db_conn() as conn:
        print(f'Imported {import_baseline(conn, args.csv_path)} unverified snapshots.')
