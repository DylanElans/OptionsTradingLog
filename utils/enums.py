from __future__ import annotations

ACTION_LABELS = {
    "STO": "Sell To Open",
    "BTO": "Buy To Open",
    "BUY_SHARES": "Buy Stock",
    "SELL_SHARES": "Sell Stock",
}

STRATEGY_LABELS = {
    "CSP": "Cash Secured Put",
    "CC": "Covered Call",
    "CALL": "Single Call",
    "PUT": "Single Put",
    "CALL_SPREAD": "Call Spread",
    "PUT_SPREAD": "Put Spread",
    "IRON_CONDOR": "Iron Condor",
    "STRANGLE": "Strangle",
    "STOCK": "Stock",
}

SUB_TYPE_LABELS = {
    "CREDIT": "Credit",
    "DEBIT": "Debit",
}

MARKETS = ["US", "HK", "CN", "CRYPTO", "OTHER"]
OPTION_RIGHTS = ["PUT", "CALL", "BOTH", "NONE"]
RESULT_STATUSES = ["OPEN", "CLOSED", "EXPIRED", "ASSIGNED"]
EXECUTION_SCORES = ["YES", "NO", "PARTIAL"]
OUTCOME_TYPES = ["CORRECT_WIN", "CORRECT_LOSS", "BAD_TRADE_WIN", "BAD_TRADE_LOSS"]
LEDGER_TYPES = ["DEPOSIT", "WITHDRAW", "FEE", "DIVIDEND", "INTEREST"]

LEDGER_TYPE_LABELS = {
    "DEPOSIT": "入金（增加资金）",
    "WITHDRAW": "出金（减少资金）",
    "FEE": "手续费（交易成本）",
    "DIVIDEND": "分红（被动收益）",
    "INTEREST": "利息（资金成本/收益）",
}

OPEN_ACTIONS = {"STO", "BTO", "BUY_SHARES"}
CLOSE_ACTIONS = {"STC", "BTC", "SELL_SHARES"}
CREDIT_ACTIONS = {"STO", "STC", "SELL_SHARES"}
DEBIT_ACTIONS = {"BTO", "BTC", "BUY_SHARES"}

DEFAULT_SUB_TYPE_BY_STRATEGY = {
    "CSP": "CREDIT",
    "CC": "CREDIT",
    "CALL": "DEBIT",
    "PUT": "DEBIT",
    "CALL_SPREAD": "DEBIT",
    "PUT_SPREAD": "DEBIT",
    "IRON_CONDOR": "CREDIT",
    "STRANGLE": "CREDIT",
}


def format_action(code: str) -> str:
    return f"{code} - {ACTION_LABELS.get(code, code)}"


def format_strategy(code: str) -> str:
    return f"{code} - {STRATEGY_LABELS.get(code, code)}"


def format_sub_type(code: str) -> str:
    return f"{code} - {SUB_TYPE_LABELS.get(code, code)}"


def format_ledger_type(code: str) -> str:
    return f"{code} - {LEDGER_TYPE_LABELS.get(code, code)}"

