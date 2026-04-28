from __future__ import annotations

from pathlib import Path
from typing import Optional
import sqlite3

import pandas as pd
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "tradinglog.db"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"


TRADE_COLUMNS = {
    "trade_date": "TEXT NOT NULL",
    "opened_date": "TEXT",
    "closed_date": "TEXT",
    "position_id": "TEXT",
    "action": "TEXT NOT NULL",
    "option_strategy": "TEXT NOT NULL",
    "sub_type": "TEXT",
    "strategy_tag": "TEXT",
    "ticker": "TEXT NOT NULL",
    "underlying_symbol": "TEXT",
    "market": "TEXT",
    "expiry_date": "TEXT",
    "strike_price": "REAL",
    "strike_price_2": "REAL",
    "strike_price_3": "REAL",
    "strike_price_4": "REAL",
    "option_right": "TEXT",
    "qty": "INTEGER NOT NULL DEFAULT 1",
    "multiplier": "INTEGER NOT NULL DEFAULT 100",
    "share_price_at_trans": "REAL",
    "premium": "REAL",
    "gross_amount": "REAL",
    "fees": "REAL DEFAULT 0",
    "net_cash_flow": "REAL",
    "iv": "REAL",
    "iv_rank": "REAL",
    "iv_percentile": "REAL",
    "delta": "REAL",
    "theta": "REAL",
    "vega": "REAL",
    "pop": "REAL",
    "dte": "INTEGER",
    "break_even": "REAL",
    "max_profit": "REAL",
    "max_loss": "REAL",
    "margin_req": "REAL",
    "buying_power_effect": "REAL",
    "entry_reason": "TEXT",
    "exit_plan": "TEXT",
    "roll_plan": "TEXT",
    "result_status": "TEXT DEFAULT 'OPEN'",
    "assigned": "INTEGER DEFAULT 0",
    "closed_pnl": "REAL",
    "annualized_return": "REAL",
    "execution_score": "TEXT",
    "outcome_type": "TEXT",
    "review_note": "TEXT",
    "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
}


LEDGER_COLUMNS = {
    "ledger_date": "TEXT NOT NULL",
    "ledger_type": "TEXT NOT NULL",
    "amount": "REAL NOT NULL",
    "note": "TEXT",
    "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
}


def get_engine():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return create_engine(DB_URL, future=True)


def _ensure_table_and_columns(conn: sqlite3.Connection, table_name: str, columns: dict, pk_sql: str) -> None:
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if table_name not in existing_tables:
        cols_sql = ",\n        ".join([pk_sql] + [f"{name} {col_sql}" for name, col_sql in columns.items()])
        conn.execute(f"CREATE TABLE {table_name} ({cols_sql})")
        return

    existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    for name, col_sql in columns.items():
        if name not in existing_cols:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {col_sql}")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_table_and_columns(conn, "trades", TRADE_COLUMNS, "id INTEGER PRIMARY KEY AUTOINCREMENT")
        _ensure_table_and_columns(conn, "cash_ledger", LEDGER_COLUMNS, "id INTEGER PRIMARY KEY AUTOINCREMENT")

        conn.executescript(
            """
            DROP TRIGGER IF EXISTS trg_trades_updated_at;
            CREATE TRIGGER trg_trades_updated_at
            AFTER UPDATE ON trades
            FOR EACH ROW
            WHEN NEW.updated_at = OLD.updated_at
            BEGIN
                UPDATE trades
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = OLD.id;
            END;
            """
        )
        conn.commit()


def insert_trade(payload: dict) -> None:
    engine = get_engine()
    cols = ", ".join(payload.keys())
    placeholders = ", ".join([f":{k}" for k in payload.keys()])
    sql = text(f"INSERT INTO trades ({cols}) VALUES ({placeholders})")
    with engine.begin() as conn:
        conn.execute(sql, payload)


def update_trade(trade_id: int, payload: dict) -> None:
    engine = get_engine()
    set_clause = ", ".join([f"{k} = :{k}" for k in payload.keys()])
    sql = text(f"UPDATE trades SET {set_clause} WHERE id = :trade_id")
    final_payload = {**payload, "trade_id": trade_id}
    with engine.begin() as conn:
        conn.execute(sql, final_payload)


def get_trades(where_sql: str = "", params: Optional[dict] = None) -> pd.DataFrame:
    engine = get_engine()
    query = "SELECT * FROM trades"
    if where_sql:
        query += f" WHERE {where_sql}"
    query += " ORDER BY trade_date DESC, id DESC"
    with engine.begin() as conn:
        return pd.read_sql(text(query), conn, params=params or {})


def get_trade_by_id(trade_id: int) -> pd.DataFrame:
    return get_trades("id = :trade_id", {"trade_id": trade_id})


def insert_ledger(ledger_date: str, ledger_type: str, amount: float, note: str = "") -> None:
    engine = get_engine()
    sql = text(
        "INSERT INTO cash_ledger (ledger_date, ledger_type, amount, note) VALUES (:ledger_date, :ledger_type, :amount, :note)"
    )
    with engine.begin() as conn:
        conn.execute(
            sql,
            {
                "ledger_date": ledger_date,
                "ledger_type": ledger_type,
                "amount": amount,
                "note": note,
            },
        )


def get_ledger() -> pd.DataFrame:
    engine = get_engine()
    with engine.begin() as conn:
        return pd.read_sql(text("SELECT * FROM cash_ledger ORDER BY ledger_date DESC, id DESC"), conn)
