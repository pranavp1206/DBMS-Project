"""
init_db.py — Database Initialization / Reset Script
Stock Market Simulation Database
Group B14 | 23CSE202 | Amrita School of Computing

Run: python init_db.py
"""

import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

def init_db():

    
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)

    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_name = os.getenv("DB_NAME", "stockmarket")
    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASSWORD", "")

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        conn = psycopg2.connect(database_url)
    else:
        conn = psycopg2.connect(host=db_host, port=db_port, dbname=db_name,
                                user=db_user, password=db_pass)
    cur = conn.cursor()

    schema_file = Path(__file__).resolve().parent / "schema.sql"
    if not schema_file.exists():
        raise FileNotFoundError(f"schema.sql not found at {schema_file}")

    print("Resetting public schema...")
    cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
    cur.execute("CREATE SCHEMA public;")
    cur.execute(f"GRANT ALL ON SCHEMA public TO {db_user};")
    cur.execute("GRANT ALL ON SCHEMA public TO public;")

    sql_content = schema_file.read_text(encoding="utf-8")
    
    # Strip \c metacommands and database create/drop statements
    filtered_lines = []
    for line in sql_content.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("\\c"):
            continue
        if trimmed.upper().startswith("DROP DATABASE"):
            continue
        if trimmed.upper().startswith("CREATE DATABASE"):
            continue
        filtered_lines.append(line)

    sql_to_execute = "\n".join(filtered_lines)

    print("Applying schema.sql...")
    cur.execute(sql_to_execute)

    print("Verifying setup...")
    cur.execute("SELECT COUNT(*) FROM users;")
    user_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM stocks;")
    stock_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM price_history;")
    price_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM portfolio_holdings;")
    port_count = cur.fetchone()[0]

    print(f"Database initialized successfully!")
    print(f"  Users:             {user_count}")
    print(f"  Stocks:            {stock_count}")
    print(f"  Price history:     {price_count}")
    print(f"  Portfolio holdings:{port_count}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    init_db()
