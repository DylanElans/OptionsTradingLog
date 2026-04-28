from __future__ import annotations

from datetime import date, timedelta
import html
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from db import (
    DB_PATH,
    get_ledger,
    get_trade_by_id,
    get_trades,
    init_db,
    insert_ledger,
    insert_trade,
    update_trade,
)
from utils.enums import (
    ACTION_LABELS,
    DEFAULT_SUB_TYPE_BY_STRATEGY,
    EXECUTION_SCORES,
    LEDGER_TYPES,
    MARKETS,
    OPTION_RIGHTS,
    OUTCOME_TYPES,
    RESULT_STATUSES,
    STRATEGY_LABELS,
    SUB_TYPE_LABELS,
    format_action,
    format_strategy,
    format_sub_type,
    format_ledger_type,
)

from utils.metrics import (
    build_equity_curve,
    build_lightweight_series,
    compute_annualized_return,
    compute_gross_amount,
    compute_net_cash_flow,
    infer_break_even,
    none_if_zero,
    normalize_text,
    safe_float,
    summarize_trades,
)


def req(label: str) -> str:
    return f"**{label}** :red[*]"


st.set_page_config(page_title="期权交易日志", page_icon="📊", layout="wide")

st.markdown(
    """
<style>
#MainMenu {visibility: hidden;}
header, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {display: none !important;}
.block-container {padding-top: 0rem !important; padding-bottom: 0.5rem !important;}
.trade-header, .trade-row {font-size: 13px;}
.trade-header {font-weight: 700; padding: 3px 0 5px 0; border-bottom: 1px solid rgba(49,51,63,0.18);}
.trade-row {padding: 2px 0; border-bottom: 1px solid rgba(49,51,63,0.10); min-height: 24px;}
.trade-row.trade-open {color: #dc2626; font-weight: 700;}
.detail-card-grid-3 {display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 10px; margin:6px 0 12px;}
.detail-card-grid-4 {display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 10px; margin:6px 0 12px;}
.detail-card {border: 1px solid rgba(49,51,63,0.12); border-radius: 14px; padding:10px 12px; background:#f8fafc; min-height:120px;}
.detail-card-title {font-size:18px; font-weight:700; margin-bottom:6px; color:#111827;}
.detail-card-row {display:flex; justify-content:space-between; margin:2px 0; font-size:16px;}
.detail-card-label {color:#6b7280; font-size:14px;}
.detail-card-value {color:#111827; font-weight:600; text-align:right;}

/* 统一按钮样式 */
.stButton > button, .stFormSubmitButton > button {
    border-radius: 10px !important;
    font-weight: 700 !important;
    padding: 0.42rem 0.85rem !important;
    border: 1px solid rgba(49,51,63,0.18) !important;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
    border-color: rgba(37,99,235,0.55) !important;
}

/* 分页按钮强制缩小 */
div[data-testid="column"] button {
    padding: 0.2rem 0.5rem !important;
    font-size: 12px !important;
    min-height: 1.8rem !important;
    border-radius: 6px !important;
}

/* 分页增强页码 */
div[data-testid="column"] button {
    padding: 0.2rem 0.55rem !important;
    font-size: 12px !important;
    min-height: 1.9rem !important;
    border-radius: 6px !important;
    white-space: nowrap !important;
}

/* 分页按钮专用：按 key 精准缩小 */
div[data-testid="stButton"] button[kind="secondary"] {
    white-space: nowrap !important;
}
div[data-testid="stButton"] button {
    line-height: 1.1 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

init_db()

# ====================== 顶部导航跳转处理 ======================
if "pending_nav" in st.session_state:
    st.session_state["nav_page"] = st.session_state["pending_nav"]
    del st.session_state["pending_nav"]

nav_options = ["录入交易日志", "交易日志显示", "修改交易日志", "交易日志回填", "日志回收站", "资金流水录入", "资金流水查看", "统计总结"]

if "nav_page" not in st.session_state or st.session_state["nav_page"] not in nav_options:
    st.session_state["nav_page"] = "录入交易日志"

page = st.sidebar.radio("导航", nav_options, key="nav_page")

if "selected_trade_id" not in st.session_state:
    st.session_state.selected_trade_id = None
if "trade_page" not in st.session_state:
    st.session_state.trade_page = 1
if "backfill_trade_id" not in st.session_state:
    st.session_state.backfill_trade_id = None
if "edit_trade_id" not in st.session_state:
    st.session_state.edit_trade_id = None
if "need_clear_selection" not in st.session_state:
    st.session_state.need_clear_selection = False
if "show_review_dialog_trade_id" not in st.session_state:
    st.session_state.show_review_dialog_trade_id = None
if "edit_save_message" not in st.session_state:
    st.session_state.edit_save_message = ""
if "edit_save_error" not in st.session_state:
    st.session_state.edit_save_error = ""
if "confirm_delete_trade_id" not in st.session_state:
    st.session_state.confirm_delete_trade_id = None
if "last_deleted_trade_id" not in st.session_state:
    st.session_state.last_deleted_trade_id = None
if "last_deleted_trade_label" not in st.session_state:
    st.session_state.last_deleted_trade_label = ""


def as_date(value, fallback=None):
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return fallback or date.today()
    return dt.date()


def generate_position_id_by_trade_date(trade_date_value) -> str:
    """
    自动生成交易日志编号：YYYYMMDD01 ~ YYYYMMDD99。
    只生成用户可见的 position_id，不修改数据库主键 id。
    """
    trade_dt = as_date(trade_date_value, date.today())
    date_str = trade_dt.strftime("%Y%m%d")

    df_today = get_trades("date(trade_date) = date(:d)", {"d": str(trade_dt)})

    used_seq = []
    if not df_today.empty and "position_id" in df_today.columns:
        for pid in df_today["position_id"].dropna().astype(str):
            pid = pid.strip()
            if pid.startswith(date_str) and len(pid) >= 10:
                tail = pid[-2:]
                if tail.isdigit():
                    used_seq.append(int(tail))

    next_seq = max(used_seq, default=0) + 1

    if next_seq > 99:
        raise ValueError(f"{date_str} 当天交易编号已超过 99 条上限，无法继续新增")

    return f"{date_str}{next_seq:02d}"


def display_trade_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["action_display"] = out["action"].fillna("").map(lambda x: f"{x} - {ACTION_LABELS.get(x, x)}")
    out["strategy_display"] = out["option_strategy"].fillna("").map(lambda x: f"{x} - {STRATEGY_LABELS.get(x, x)}")
    out["sub_type_display"] = out["sub_type"].fillna("").map(lambda x: f"{x} - {SUB_TYPE_LABELS.get(x, x)}" if x else "")
    return out


def display_ledger_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "ledger_type" in out.columns:
        out["ledger_type_display"] = out["ledger_type"].fillna("").map(format_ledger_type)
    return out


def get_default_option_right(strategy: str) -> str:
    if strategy == "STOCK": return "NONE"
    if strategy in ["PUT", "CSP", "PUT_SPREAD"]: return "PUT"
    if strategy in ["CALL", "CC", "CALL_SPREAD"]: return "CALL"
    return "BOTH"


def get_strike_labels(strategy: str) -> list[str]:
    if strategy == "CALL": return ["Call Strike Price"]
    if strategy == "PUT": return ["Put Strike Price"]
    if strategy == "CALL_SPREAD": return ["Long Call Price", "Short Call Price"]
    if strategy == "PUT_SPREAD": return ["Long Put Price", "Short Put Price"]
    if strategy == "IRON_CONDOR": return ["Long Put Price", "Short Put Price", "Short Call Price", "Long Call Price"]
    if strategy == "STRANGLE": return ["Short Put Price", "Short Call Price"]
    if strategy == "CSP": return ["Short Put Price"]
    if strategy == "CC": return ["Short Call Price"]
    if strategy == "STOCK": return []
    return ["Strike Price"]


def validate_trade_form(option_strategy: str, sub_type, ticker: str, expiry_date, strike_values):
    errors = []
    if not (ticker or "").strip(): errors.append("Ticker / 标的 为必填项")
    if option_strategy != "STOCK" and not sub_type: errors.append("子类型 为必填项")
    if option_strategy != "STOCK" and not expiry_date: errors.append("到期日 为必填项")
    labels = get_strike_labels(option_strategy)
    for i, label in enumerate(labels):
        v = strike_values[i] if i < len(strike_values) else None
        if v is None or float(v) <= 0:
            errors.append(f"{label} 必须大于 0")
    return errors


def validate_backfill_form(closed_date, result_status, execution_score, closed_pnl, outcome_type):
    errors = []
    if not closed_date: errors.append("平仓日期 为必填项")
    if not result_status: errors.append("结果状态 为必填项")
    if not execution_score: errors.append("是否按计划执行 为必填项")
    if closed_pnl is None: errors.append("已实现盈亏 为必填项")
    if not outcome_type: errors.append("结果归因 为必填项")
    return errors


def zh_col_name(col: str) -> str:
    mapping = {
        "id": "ID", "trade_date": "交易日期", "position_id": "编号", "ticker": "标的",
        "action_display": "动作", "strategy_display": "策略", "sub_type_display": "子类型",
        "expiry_date": "到期日", "strike_price": "执行价1", "strike_price_2": "执行价2",
        "strike_price_3": "执行价3", "strike_price_4": "执行价4", "qty": "数量",
        "premium": "权利金", "fees": "手续费", "net_cash_flow": "净现金流",
        "result_status": "状态", "closed_pnl": "已实现盈亏",
    }
    return mapping.get(col, col)


def render_detail_cards(detail_row: pd.Series, show_review: bool = True, columns: int = 3):
    if detail_row is None or detail_row.empty:
        return

    def val(field: str, default: str = ""):
        v = detail_row.get(field, default)
        return "" if pd.isna(v) else str(v)

    groups = {
        "基本信息": [
            ("编号", val("id")), ("持仓ID", val("position_id")), ("交易日期", val("trade_date")),
            ("开仓日期", val("opened_date")), ("标的", val("ticker")), ("市场", val("market")),
            ("动作", f"{val('action')} - {ACTION_LABELS.get(val('action'), val('action'))}" if val("action") else ""),
            ("策略", f"{val('option_strategy')} - {STRATEGY_LABELS.get(val('option_strategy'), val('option_strategy'))}" if val("option_strategy") else ""),
        ],
        "合约信息": [
            ("子类型", f"{val('sub_type')} - {SUB_TYPE_LABELS.get(val('sub_type'), val('sub_type'))}" if val("sub_type") else ""),
            ("到期日", val("expiry_date")),
            ("执行价1", val("strike_price")), ("执行价2", val("strike_price_2")),
            ("执行价3", val("strike_price_3")), ("执行价4", val("strike_price_4")),
            ("方向", val("option_right")), ("DTE", val("dte")),
            ("开仓理由", val("entry_reason")),
            ("退出计划", val("exit_plan")),
            ("滚动计划", val("roll_plan")),
        ],
        "交易信息": [
            ("数量", val("qty")), ("乘数", val("multiplier")), ("标的价格", val("share_price_at_trans")),
            ("权利金 / 单价", val("premium")), ("毛额", val("gross_amount")), ("手续费", val("fees")),
            ("净现金流", val("net_cash_flow")), ("策略标签", val("strategy_tag")),
        ],
        "风险与Greeks": [
            ("IV", val("iv")), ("IV Rank", val("iv_rank")), ("IV Percentile", val("iv_percentile")),
            ("Delta", val("delta")), ("Theta", val("theta")), ("Vega", val("vega")), ("PoP %", val("pop")),
            ("盈亏平衡", val("break_even")), ("最大收益", val("max_profit")), ("最大风险", val("max_loss")),
            ("保证金需求", val("margin_req")), ("Buying Power Effect", val("buying_power_effect")),
        ],
    }

    if show_review:
        groups["复盘信息"] = [
            ("结果状态", val("result_status")), ("平仓日期", val("closed_date")),
            ("已实现盈亏", val("closed_pnl")), ("年化收益", val("annualized_return")),
            ("是否被指派", val("assigned")), ("执行评分", val("execution_score")),
            ("结果归因", val("outcome_type")), ("复盘说明", val("review_note")),
        ]

    #st.markdown("### 详细信息")
    grid_class = "detail-card-grid-4" if columns == 4 else "detail-card-grid-3"

    cards_html = []
    for title, items in groups.items():
        shown = [(k, v) for k, v in items if str(v).strip()]
        if not shown: continue
        rows_html = "".join(
            f'<div class="detail-card-row"><div class="detail-card-label">{html.escape(k)}</div>'
            f'<div class="detail-card-value">{html.escape(v)}</div></div>'
            for k, v in shown
        )
        cards_html.append(f'<div class="detail-card"><div class="detail-card-title">{html.escape(title)}</div>{rows_html}</div>')

    st.markdown(f'<div class="{grid_class}">{"".join(cards_html)}</div>', unsafe_allow_html=True)


@st.dialog("编辑复盘说明")
def edit_review_dialog(trade_id: int, current_note: str):
    st.write(f"**交易编号：{trade_id}**")
    new_note = st.text_area(
        "复盘说明",
        value=current_note,
        height=300,
        placeholder="在这里输入详细的复盘内容、经验教训、改进点等..."
    )

    col1, col2 = st.columns(2)
    if col1.button("✅ 确定保存", type="primary", use_container_width=True):
        if new_note.strip() != current_note.strip():
            update_trade(trade_id, {"review_note": new_note.strip() or None})
            st.success("✅ 复盘说明已更新！")
        st.session_state.show_review_dialog_trade_id = None
        st.rerun()

    if col2.button("❌ 取消", use_container_width=True):
        st.session_state.show_review_dialog_trade_id = None
        st.rerun()


def delete_trade_by_id(trade_id: int) -> None:
    """删除一条交易日志。只删除 trades 表中的交易记录。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (int(trade_id),))
        conn.commit()


@st.dialog("确认删除日志")
def delete_trade_confirm_dialog(trade_id: int, position_id: str, ticker: str, strategy_text: str):
    st.warning(f"确认删除 {position_id} 编号 {ticker} 标的 {strategy_text} 策略 的日志？")
    st.caption("删除后无法在系统内直接恢复，请确认已备份数据库。")

    col1, col2 = st.columns(2)
    if col1.button("🗑️ 确认删除", type="primary", use_container_width=True, key=f"confirm_delete_{trade_id}"):
        delete_trade_by_id(int(trade_id))
        st.session_state.selected_trade_id = None
        st.session_state.edit_trade_id = None
        st.session_state.need_clear_selection = True
        st.session_state["pending_nav"] = "交易日志显示"
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.toast(f"已删除日志：{position_id}", icon="🗑️")
        st.rerun()

    if col2.button("取消", use_container_width=True, key=f"cancel_delete_{trade_id}"):
        st.rerun()


def sync_visible_row_checks(visible_ids: list[int]):
    selected = st.session_state.get("selected_trade_id")
    for rid in visible_ids:
        st.session_state[f"trade_select_{rid}"] = selected == rid


def handle_trade_checkbox_change(trade_id: int, visible_ids: list[int]):
    key = f"trade_select_{trade_id}"
    checked = bool(st.session_state.get(key, False))
    if checked:
        st.session_state.selected_trade_id = trade_id
        for rid in visible_ids:
            st.session_state[f"trade_select_{rid}"] = (rid == trade_id)
    else:
        if st.session_state.get("selected_trade_id") == trade_id:
            st.session_state.selected_trade_id = None
        st.session_state[key] = False


def update_backfill_pnl(trade_id: int, action: str, premium: float, qty: int, multiplier: int):
    close_key = f"bf_close_price_{trade_id}"
    pnl_key = f"bf_closed_pnl_{trade_id}"
    close_price = float(st.session_state.get(close_key, 0.0) or 0.0)

    if close_price <= 0:
        return

    action = (action or "").upper()
    if action == "BTO":
        st.session_state[pnl_key] = round((close_price - premium) * qty * multiplier, 2)
    elif action == "STO":
        st.session_state[pnl_key] = round((premium - close_price) * qty * multiplier, 2)


def _get_db_path_from_db_module():
    """尽量复用 db.py 里的数据库路径，避免连错库。"""
    try:
        import db
        for name in ["DB_PATH", "DATABASE_PATH", "db_path"]:
            if hasattr(db, name):
                p = getattr(db, name)
                return str(p)
        if hasattr(db, "engine"):
            url = str(db.engine.url)
            if url.startswith("sqlite:///"):
                return url.replace("sqlite:///", "", 1)
    except Exception:
        pass
    return r"D:\apps\TradingLog\data\tradinglog.db"


def _sqlite_connect():
    conn = sqlite3.connect(_get_db_path_from_db_module())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_soft_delete_columns():
    """为撤销删除准备字段；已存在则忽略。"""
    with _sqlite_connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "is_deleted" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN is_deleted INTEGER DEFAULT 0")
        if "deleted_at" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN deleted_at TEXT")
        if "deleted_backup_status" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN deleted_backup_status TEXT")
        conn.commit()


def soft_delete_trade(trade_id: int):
    """软删除：不物理删除，支持撤销。"""
    ensure_soft_delete_columns()
    with _sqlite_connect() as conn:
        row = conn.execute("SELECT result_status FROM trades WHERE id = ?", (int(trade_id),)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE trades
            SET is_deleted = 1,
                deleted_at = datetime('now'),
                deleted_backup_status = result_status,
                result_status = 'DELETED'
            WHERE id = ?
            """,
            (int(trade_id),),
        )
        conn.commit()


def undo_delete_trade(trade_id: int):
    """撤销软删除。"""
    ensure_soft_delete_columns()
    with _sqlite_connect() as conn:
        conn.execute(
            """
            UPDATE trades
            SET is_deleted = 0,
                deleted_at = NULL,
                result_status = COALESCE(deleted_backup_status, 'OPEN'),
                deleted_backup_status = NULL
            WHERE id = ?
            """,
            (int(trade_id),),
        )
        conn.commit()


def permanently_delete_trade(trade_id: int):
    """彻底删除：从数据库物理删除，不可撤销。"""
    ensure_soft_delete_columns()
    with _sqlite_connect() as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (int(trade_id),))
        conn.commit()


# ====================== 页面逻辑 ======================

if page == "录入交易日志":
    # st.subheader("新增交易记录")
    st.caption("新增交易记   --  :red[*] 为必填项")

    # 复位按钮不能在控件创建后直接改 top_*。
    # 用 nonce 强制换一组 widget key，确保所有录入控件真正回到默认值。
    if "trade_form_nonce" not in st.session_state:
        st.session_state["trade_form_nonce"] = 0

    if st.session_state.get("pending_reset_trade_form", False):
        st.session_state["trade_form_nonce"] += 1
        st.session_state["pending_reset_trade_form"] = False

    trade_form_nonce = st.session_state["trade_form_nonce"]

    top = st.columns(5)
    opened_date = top[0].date_input(req("开仓日期"), value=date.today(), key=f"top_opened_date_{trade_form_nonce}")
    trade_date = opened_date  # 隐藏“交易日期”，交易日期数据默认等于开仓日期
    expiry_date = top[1].date_input(req("到期日"), value=date.today(), key=f"top_expiry_date_{trade_form_nonce}")
    action = top[2].selectbox(req("动作"), options=list(ACTION_LABELS.keys()), format_func=format_action, key=f"top_action_{trade_form_nonce}")
    option_strategy = top[3].selectbox(req("期权策略"), options=list(STRATEGY_LABELS.keys()), format_func=format_strategy, key=f"top_option_strategy_{trade_form_nonce}")

    default_sub_type = DEFAULT_SUB_TYPE_BY_STRATEGY.get(option_strategy)
    if option_strategy == "STOCK":
        top[4].text_input("子类型", value="N/A", disabled=True, key=f"top_sub_type_na_{trade_form_nonce}")
        sub_type = None
    else:
        sub_type_options = list(SUB_TYPE_LABELS.keys())
        default_index = sub_type_options.index(default_sub_type) if default_sub_type in sub_type_options else 0
        sub_type = top[4].selectbox(req("子类型"), options=sub_type_options, index=default_index, format_func=format_sub_type, key=f"top_sub_type_{trade_form_nonce}")

    with st.form(f"trade_form_{trade_form_nonce}", clear_on_submit=True):
        # 第1行：Ticker/标的，到期日，标的价格，手续费，DTE
        r2 = st.columns(5)
        ticker = r2[0].text_input(req("Ticker / 标的"), value="")
        r2[1].text_input(req("到期日"), value=str(expiry_date), disabled=True)
        share_price_at_trans = r2[2].number_input("标的价格", value=0.0, step=0.01)
        fees = r2[3].number_input("手续费", value=0.0, step=0.01)

        # DTE 自动计算：到期日 - 开仓日期
        dte = max((expiry_date - opened_date).days, 0)
        r2[4].number_input("DTE", min_value=0, value=int(dte), disabled=True)

        # 第2行：权利金 / 单价，执行价（最多4个）
        r3 = st.columns(5)
        premium = r3[0].number_input("权利金 / 单价", value=0.0, step=0.01)
        strike_labels = get_strike_labels(option_strategy)
        strike_values = [None] * 4
        if option_strategy == "STOCK":
            for i in range(1, 5):
                r3[i].text_input("", value="", disabled=True, key=f"strike_stock_empty_{i}_{trade_form_nonce}")
        else:
            for idx, label in enumerate(strike_labels):
                strike_values[idx] = r3[idx + 1].number_input(req(label), value=0.0, step=0.5, key=f"form_strike_{idx+1}_{trade_form_nonce}")
            for idx in range(len(strike_labels), 4):
                r3[idx + 1].text_input("", value="", disabled=True, key=f"form_strike_disabled_{idx+1}_{trade_form_nonce}")

        strike_price, strike_price_2, strike_price_3, strike_price_4 = strike_values

        # 第3行：Delta，Theta，IV，IV Rank，Vega
        r4 = st.columns(5)
        delta = r4[0].number_input("Delta", value=0.0, step=0.01)
        theta = r4[1].number_input("Theta", value=0.0, step=0.01)
        iv = r4[2].number_input("IV", value=0.0, step=0.01)
        iv_rank = r4[3].number_input("IV Rank", value=0.0, step=0.01)
        vega = r4[4].number_input("Vega", value=0.0, step=0.01)
        iv_percentile = 0.0  # 界面不再录入 IV Percentile

        # 第4行：PoP，最大收益，最大风险，底层资产，市场
        r5 = st.columns(5)
        pop = r5[0].number_input("PoP % [盈利概率]", value=0.0, step=1.0)
        max_profit = r5[1].number_input("最大收益", value=0.0, step=1.0)
        max_loss = r5[2].number_input("最大风险", value=0.0, step=1.0)
        underlying_symbol = r5[3].text_input("Underlying Symbol [底层资产]", value="")
        market = r5[4].selectbox("市场", MARKETS)

        # 第5行：保证金需求，方向，数量，乘数，策略标签
        r6 = st.columns(5)
        margin_req = r6[0].number_input("保证金需求", value=0.0, step=1.0)
        option_right_default = get_default_option_right(option_strategy)
        option_right = r6[1].selectbox("方向", OPTION_RIGHTS, index=OPTION_RIGHTS.index(option_right_default))
        qty = r6[2].number_input(req("数量"), min_value=1, value=1)
        multiplier = r6[3].number_input(req("乘数"), min_value=1, value=100)
        strategy_tag = r6[4].text_input("策略标签", value="")
        position_id = ""  # Position ID 后台自动生成，界面不再录入
        buying_power_effect = 0.0  # 界面不再录入 Buying Power Effect

        # 第6行：开仓理由，退出计划，滚动计划
        t1 = st.columns(3)
        entry_reason = t1[0].text_area("开仓理由", height=100)
        exit_plan = t1[1].text_area("退出计划", height=100)
        roll_plan = t1[2].text_area("滚动计划", height=100)

        col_submit, col_reset = st.columns(2)
        submitted = col_submit.form_submit_button("💾 保存交易")
        reset_clicked = col_reset.form_submit_button("🔄 复位")

        if reset_clicked:
            # 设置待复位标记，下一轮 rerun 在控件创建前删除 top_ / form_ 状态
            st.session_state["pending_reset_trade_form"] = True
            st.rerun()

        if submitted:
            validation_errors = validate_trade_form(option_strategy, sub_type, ticker, expiry_date, strike_values)

            if expiry_date < opened_date:
                validation_errors.append("到期日必须大于或等于开仓日期")

            if validation_errors:
                for msg in validation_errors:
                    st.error(msg)
            else:
                try:
                    auto_position_id = position_id.strip() or generate_position_id_by_trade_date(trade_date)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()
                gross_amount = compute_gross_amount(premium, int(qty), int(multiplier))
                net_cash_flow = compute_net_cash_flow(action, premium, int(qty), int(multiplier), fees)
                break_even = infer_break_even(option_strategy, option_right, none_if_zero(strike_price), premium, share_price_at_trans)

                payload = {
                    "trade_date": str(trade_date), "opened_date": str(opened_date), "position_id": auto_position_id,
                    "action": action, "option_strategy": option_strategy, "sub_type": sub_type,
                    "strategy_tag": normalize_text(strategy_tag), "ticker": ticker.strip().upper(),
                    "underlying_symbol": normalize_text(underlying_symbol), "market": market,
                    "expiry_date": str(expiry_date),
                    "strike_price": none_if_zero(strike_price), "strike_price_2": none_if_zero(strike_price_2),
                    "strike_price_3": none_if_zero(strike_price_3), "strike_price_4": none_if_zero(strike_price_4),
                    "option_right": option_right, "qty": int(qty), "multiplier": int(multiplier),
                    "share_price_at_trans": none_if_zero(share_price_at_trans), "premium": none_if_zero(premium),
                    "gross_amount": gross_amount, "fees": safe_float(fees, 0.0), "net_cash_flow": net_cash_flow,
                    "iv": none_if_zero(iv), "iv_rank": none_if_zero(iv_rank), "iv_percentile": none_if_zero(iv_percentile),
                    "delta": none_if_zero(delta), "theta": none_if_zero(theta), "vega": none_if_zero(vega),
                    "pop": none_if_zero(pop), "dte": int(dte), "break_even": break_even,
                    "max_profit": none_if_zero(max_profit), "max_loss": none_if_zero(max_loss),
                    "margin_req": none_if_zero(margin_req), "buying_power_effect": none_if_zero(buying_power_effect),
                    "entry_reason": normalize_text(entry_reason), "exit_plan": normalize_text(exit_plan),
                    "roll_plan": normalize_text(roll_plan),
                }
                insert_trade(payload)
                st.success(f"交易记录已保存，Position ID: {auto_position_id}")





elif page == "日志回收站":
    st.subheader("日志回收站")
    st.caption("可恢复软删除日志；彻底删除后不可撤销。")

    ensure_soft_delete_columns()

    deleted_df = get_trades("COALESCE(is_deleted, 0) = 1", {})
    if deleted_df.empty:
        st.info("回收站为空。")
        st.session_state["recycle_selected_trade_id"] = None
    else:
        deleted_df = display_trade_df(deleted_df)

        table_cols = [
            "position_id",
            "ticker",
            "strategy_display",
            "result_status",
            "deleted_backup_status",
            "deleted_at",
            "trade_date",
            "expiry_date",
            "premium",
            "net_cash_flow",
            "closed_pnl",
        ]
        table_cols = [c for c in table_cols if c in deleted_df.columns]

        header_names = {
            "position_id": "编号",
            "ticker": "标的",
            "strategy_display": "策略",
            "result_status": "当前状态",
            "deleted_backup_status": "删除前状态",
            "deleted_at": "删除时间",
            "trade_date": "交易日期",
            "expiry_date": "到期日",
            "premium": "权利金",
            "net_cash_flow": "净现金流",
            "closed_pnl": "已实现盈亏",
        }

        if "recycle_selected_trade_id" not in st.session_state:
            st.session_state["recycle_selected_trade_id"] = None

        visible_ids = deleted_df["id"].astype(int).tolist()

        def handle_recycle_checkbox_change(trade_id: int, ids: list[int]):
            key = f"recycle_select_{trade_id}"
            checked = bool(st.session_state.get(key, False))
            if checked:
                st.session_state["recycle_selected_trade_id"] = trade_id
                for rid in ids:
                    st.session_state[f"recycle_select_{rid}"] = (rid == trade_id)
            else:
                if st.session_state.get("recycle_selected_trade_id") == trade_id:
                    st.session_state["recycle_selected_trade_id"] = None
                st.session_state[key] = False

        selected_id_now = st.session_state.get("recycle_selected_trade_id")
        for rid in visible_ids:
            st.session_state[f"recycle_select_{rid}"] = (selected_id_now == rid)

        header_widths = [0.7, 1.2, 1.1, 1.7, 1.0, 1.1, 1.4, 1.2, 1.2, 1.0, 1.2, 1.2]
        header_cols = st.columns(header_widths[:len(table_cols) + 1])
        header_cols[0].markdown('<div class="trade-header">选择</div>', unsafe_allow_html=True)
        for i, col in enumerate(table_cols, start=1):
            header_cols[i].markdown(
                f'<div class="trade-header">{header_names.get(col, col)}</div>',
                unsafe_allow_html=True,
            )

        for _, row in deleted_df.iterrows():
            rid = int(row["id"])
            row_cols = st.columns(header_widths[:len(table_cols) + 1])
            row_cols[0].checkbox(
                "",
                key=f"recycle_select_{rid}",
                on_change=handle_recycle_checkbox_change,
                args=(rid, visible_ids),
                label_visibility="collapsed",
            )

            for i, col in enumerate(table_cols, start=1):
                value = row.get(col, "")
                if pd.isna(value):
                    value = ""
                row_cols[i].markdown(
                    f'<div class="trade-row">{html.escape(str(value))}</div>',
                    unsafe_allow_html=True,
                )

        selected_id = st.session_state.get("recycle_selected_trade_id")
        if selected_id:
            selected_row_df = deleted_df[deleted_df["id"].astype(int) == int(selected_id)]
            if not selected_row_df.empty:
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                selected_row = selected_row_df.iloc[0]
                render_detail_cards(selected_row, show_review=True, columns=3)

                c1, c2, c3 = st.columns([1.5, 1.5, 8])
                if c1.button("↩️ 恢复日志", key=f"restore_deleted_{int(selected_id)}", type="primary"):
                    undo_delete_trade(int(selected_id))
                    st.session_state["recycle_selected_trade_id"] = None
                    st.success("已恢复日志。")
                    st.rerun()

                if c2.button("🗑️ 彻底删除", key=f"permanent_delete_prepare_{int(selected_id)}", type="secondary"):
                    st.session_state["confirm_permanent_delete_id"] = int(selected_id)
                    st.rerun()

                if st.session_state.get("confirm_permanent_delete_id") == int(selected_id):
                    position_id = str(selected_row.get("position_id") or "")
                    ticker = str(selected_row.get("ticker") or "")
                    strategy = str(selected_row.get("option_strategy") or "")
                    st.error(f"确认彻底删除？编号：{position_id} ｜ 标的：{ticker} ｜ 策略：{strategy}。此操作不可撤销。")
                    cc1, cc2, _ = st.columns([1.5, 1.5, 8])
                    if cc1.button("确认彻底删除", key=f"permanent_delete_confirm_{int(selected_id)}", type="secondary"):
                        permanently_delete_trade(int(selected_id))
                        st.session_state["confirm_permanent_delete_id"] = None
                        st.session_state["recycle_selected_trade_id"] = None
                        st.success("已彻底删除。")
                        st.rerun()
                    if cc2.button("取消", key=f"permanent_delete_cancel_{int(selected_id)}"):
                        st.session_state["confirm_permanent_delete_id"] = None
                        st.rerun()


elif page == "资金流水录入":
    st.subheader("资金流水录入")
    st.caption(":red[*] 为必填项")

    with st.form("ledger_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        ledger_date = c1.date_input(req("日期"), value=date.today(), key="ledger_date")
        ledger_type = c2.selectbox(req("类型"), LEDGER_TYPES, format_func=format_ledger_type)
        amount = c3.number_input(req("金额"), value=0.0, step=1.0)
        note = st.text_input("备注")

        if st.form_submit_button("💾 保存流水"):
            insert_ledger(str(ledger_date), ledger_type, float(amount), note)
            st.success("资金流水已保存")


elif page == "资金流水查看":
    st.subheader("资金流水查看")
    ledger_df = get_ledger()
    if ledger_df.empty:
        st.info("暂无资金流水")
    else:
        show_ledger_df = display_ledger_df(ledger_df)
        st.dataframe(show_ledger_df, use_container_width=True, height=520)


elif page == "交易日志显示":
    #st.subheader("交易日志")

    if st.session_state.get("need_clear_selection", False):
        st.session_state.selected_trade_id = None
        for key in list(st.session_state.keys()):
            if key.startswith("trade_select_"):
                st.session_state[key] = False
        st.session_state.need_clear_selection = False

    today = date.today()
    default_start = today - timedelta(days=30)

    c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 2, 1.5, 1.5, 1.5, 1.5, 1])
    start_date = c1.date_input("开始日期", value=default_start, key="log_start_date")
    end_date = c2.date_input("结束日期", value=today, key="log_end_date")
    ticker_filter = c3.text_input("Ticker筛选", key="ticker_filter")
    status_options = ["ALL"] + RESULT_STATUSES
    default_status_index = status_options.index("OPEN") if "OPEN" in status_options else 0
    status_filter = c4.selectbox("状态", status_options, index=default_status_index, key="status_filter")
    strategy_filter = c5.selectbox("策略", ["ALL"] + list(STRATEGY_LABELS.keys()), 
                                   format_func=lambda x: x if x == "ALL" else format_strategy(x), key="strategy_filter")
    sub_type_filter = c6.selectbox("子类型", ["ALL"] + list(SUB_TYPE_LABELS.keys()), 
                                   format_func=lambda x: x if x == "ALL" else format_sub_type(x), key="sub_type_filter")

    if c7.button("🔄 刷新数据", key="refresh_btn"):
        st.session_state.need_clear_selection = True
        st.rerun()

    # 查询条件变化时，清空当前选中记录，隐藏下方详情卡片
    current_log_filter = (
        str(start_date),
        str(end_date),
        ticker_filter.strip().upper(),
        status_filter,
        strategy_filter,
        sub_type_filter,
    )

    if "last_log_filter" not in st.session_state:
        st.session_state.last_log_filter = current_log_filter
    elif st.session_state.last_log_filter != current_log_filter:
        st.session_state.selected_trade_id = None
        for key in list(st.session_state.keys()):
            if key.startswith("trade_select_"):
                st.session_state[key] = False
        st.session_state.last_log_filter = current_log_filter

    clauses = []
    params = {}
    if start_date: clauses.append("date(trade_date) >= date(:start_date)"); params["start_date"] = str(start_date)
    if end_date: clauses.append("date(trade_date) <= date(:end_date)"); params["end_date"] = str(end_date)
    if ticker_filter.strip(): clauses.append("ticker LIKE :ticker"); params["ticker"] = f"%{ticker_filter.strip().upper()}%"
    if status_filter != "ALL": clauses.append("result_status = :result_status"); params["result_status"] = status_filter
    if strategy_filter != "ALL": clauses.append("option_strategy = :option_strategy"); params["option_strategy"] = strategy_filter
    if sub_type_filter != "ALL": clauses.append("sub_type = :sub_type"); params["sub_type"] = sub_type_filter

    where_sql = " AND ".join(clauses) if clauses else None
    df = get_trades(where_sql, params)
    if not df.empty and "is_deleted" in df.columns:
        df = df[pd.to_numeric(df["is_deleted"], errors="coerce").fillna(0).astype(int) == 0]
    show_df = display_trade_df(df)

    page_size = 15
    total_rows = len(show_df)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    if st.session_state.trade_page > total_pages: st.session_state.trade_page = total_pages
    if st.session_state.trade_page < 1: st.session_state.trade_page = 1

    info_col, prev_col, page_col, next_col, _ = st.columns([5.2, 1.35, 1.25, 1.35, 7.2])
    # 计算当前筛选结果的已实现盈亏汇总
    total_pnl = show_df["closed_pnl"].fillna(0).sum() if "closed_pnl" in show_df.columns else 0
    # 显示
    info_col.caption(f"当前筛选结果：{total_rows} 条 ｜ 已实现盈亏汇总：{total_pnl:.2f}")

    if prev_col.button("上一页", disabled=st.session_state.trade_page <= 1, key="prev_page", use_container_width=True):
        st.session_state.trade_page -= 1; st.rerun()

    page_col.markdown(
        f"<div style='text-align:center; font-size:13px; font-weight:700; padding-top:8px; white-space:nowrap;'>第 {st.session_state.trade_page} / {total_pages} 页</div>",
        unsafe_allow_html=True,
    )

    if next_col.button("下一页", disabled=st.session_state.trade_page >= total_pages, key="next_page", use_container_width=True):
        st.session_state.trade_page += 1; st.rerun()

    start_idx = (st.session_state.trade_page - 1) * page_size
    end_idx = start_idx + page_size
    page_df = show_df.iloc[start_idx:end_idx].copy()

    table_cols = [
        "position_id",        # 编号
        "ticker",             # 标的
        "strategy_display",   # 策略
        "result_status",      # 状态        
        "net_cash_flow",      # 净现金流
        "expiry_date",        # 到期日
        "action_display",     # 动作        
        "sub_type_display",   # 子类型
        "premium",            # 权利金
        "strike_price",       # 执行价1
        "strike_price_2",     # 执行价2
        "strike_price_3",     # 执行价3
        "strike_price_4",     # 执行价4
        "qty",                # 数量
        "fees",               # 手续费
        "closed_pnl",         # 已实现盈亏
        "trade_date",         # 交易日期
    ]

    table_cols = [c for c in table_cols if c in page_df.columns]

    header_widths = [
    0.6,   # 选择
    1.2,   # 编号
    1.2,   # 标的
    1.5,   # 策略
    1.0,   # 状态
    1.2,   # 净现金流
    1.1,   # 到期日
    1.5,   # 动作
    1.5,   # 子类型
    1.0,   # 权利金
    1.0,   # 执行价1
    1.0,   # 执行价2
    1.0,   # 执行价3
    1.0,   # 执行价4
    0.8,   # 数量
    0.9,   # 手续费
    1.1,   # 已实现盈亏
    1.2,   # 交易日期
]

    visible_ids = page_df["id"].astype(int).tolist() if not page_df.empty else []
    sync_visible_row_checks(visible_ids)

    header_cols = st.columns(header_widths[:len(table_cols) + 1])
    header_cols[0].markdown('<div class="trade-header">选择</div>', unsafe_allow_html=True)
    for i, col in enumerate(table_cols, start=1):
        header_cols[i].markdown(f'<div class="trade-header">{zh_col_name(col)}</div>', unsafe_allow_html=True)

    for _, row in page_df.iterrows():
        rid = int(row["id"])
        row_cols = st.columns(header_widths[:len(table_cols) + 1])
        row_cols[0].checkbox(
            "",
            key=f"trade_select_{rid}",
            on_change=handle_trade_checkbox_change,
            args=(rid, visible_ids),
            label_visibility="collapsed"
        )

        for i, col in enumerate(table_cols, start=1):
            value = row.get(col, "")
            if pd.isna(value):
                value = ""

            row_class = "trade-row"
            style_extra = ""

            # OPEN 状态红色加粗
            if col == "result_status" and str(value).upper() == "OPEN":
                row_class = "trade-row trade-open"

            # 到期日距离今天 0~3 天：蓝色加粗
            if col == "expiry_date" and str(value).strip():
                try:
                    exp_date = pd.to_datetime(value).date()
                    days_left = (exp_date - date.today()).days
                    if 0 <= days_left <= 5:
                        style_extra = "color:#2563eb;font-weight:700;"
                except Exception:
                    pass

            # 普通文本显示，不再给 position_id 增加链接
            row_cols[i].markdown(
                f'<div class="{row_class}" style="{style_extra}">{html.escape(str(value))}</div>',
                unsafe_allow_html=True,
            )

    selected_trade_id = st.session_state.get("selected_trade_id")
    if selected_trade_id:
        detail = get_trade_by_id(int(selected_trade_id))
        if not detail.empty:
            #st.divider()
            render_detail_cards(detail.iloc[0], show_review=True, columns=3)

            # ====================== 按钮区域（按要求修改） ======================
            result_status = str(detail.iloc[0].get("result_status", "")).upper()
            trade_id = int(selected_trade_id)
            current_note = detail.iloc[0].get("review_note", "")

            if result_status == "OPEN":
                # 未平仓：显示修改、编辑、平仓按钮
                btn_cols = st.columns([2, 2, 2, 6])
                if btn_cols[0].button("📝 修改交易日志", key=f"goto_edit_open_{trade_id}", type="primary"):
                    st.session_state.edit_trade_id = trade_id
                    try:
                        st.query_params["edit_trade_id"] = str(trade_id)
                    except Exception:
                        pass
                    st.session_state["pending_nav"] = "修改交易日志"
                    st.rerun()

                btn_cols[1].markdown("&nbsp;")

                if btn_cols[2].button("✅ 平仓回填信息", key=f"goto_backfill_open_{trade_id}"):
                    st.session_state.backfill_trade_id = trade_id
                    try:
                        st.query_params["trade_id"] = str(trade_id)
                    except Exception:
                        pass
                    st.session_state["pending_nav"] = "交易日志回填"
                    st.rerun()
            else:
                # 已平仓：修改、编辑、回填按钮
                btn_cols = st.columns([2, 2, 2, 6])
                if btn_cols[0].button("📝 修改交易日志", key=f"goto_edit_closed_{trade_id}", type="primary"):
                    st.session_state.edit_trade_id = trade_id
                    try:
                        st.query_params["edit_trade_id"] = str(trade_id)
                    except Exception:
                        pass
                    st.session_state["pending_nav"] = "修改交易日志"
                    st.rerun()

                if result_status == "CLOSED":
                    if btn_cols[1].button(
                        "✏️ 编辑复盘说明",
                        key=f"edit_review_closed_{trade_id}",
                        type="secondary"
                    ):
                        st.session_state.show_review_dialog_trade_id = trade_id
                        st.rerun()
                else:
                    btn_cols[1].markdown("&nbsp;")


            dialog_trade_id = st.session_state.get("show_review_dialog_trade_id")
            if dialog_trade_id == trade_id and result_status == "CLOSED":
                edit_review_dialog(trade_id, current_note)


elif page == "修改交易日志":
    #st.subheader("修改交易日志")
    #st.caption(" :red[修改交易日志] - :red[*] 为必填项")

    if st.session_state.get("edit_save_message"):
        st.success(st.session_state["edit_save_message"])
        st.session_state["edit_save_message"] = ""
    if st.session_state.get("edit_save_error"):
        st.error(st.session_state["edit_save_error"])
        st.session_state["edit_save_error"] = ""

    query_edit_trade_id = None
    try:
        qp_val = st.query_params.get("edit_trade_id")
        if qp_val:
            query_edit_trade_id = int(qp_val)
    except Exception:
        query_edit_trade_id = None

    target_trade_id = st.session_state.get("edit_trade_id") or query_edit_trade_id

    if not target_trade_id:
        st.info("请从“交易日志显示”页面点击持仓ID链接，或点击“修改交易日志”按钮进入此页面。")
    else:
        detail_df = get_trade_by_id(int(target_trade_id))
        if detail_df.empty:
            st.warning(f"未找到交易记录：{target_trade_id}")
        else:
            target_trade = detail_df.iloc[0]
            render_detail_cards(target_trade, show_review=False, columns=4)

            #st.divider()
            #st.markdown("### 修改内容")

            current_strategy = str(target_trade.get("option_strategy") or "CALL")
            current_sub_type = target_trade.get("sub_type")
            current_expiry = as_date(target_trade.get("expiry_date"), date.today())
            strike_labels = get_strike_labels(current_strategy)
            default_option_right = str(target_trade.get("option_right") or get_default_option_right(current_strategy))

            with st.form(f"edit_trade_form_{int(target_trade_id)}"):
                top = st.columns(5)
                trade_date = top[0].date_input(req("交易日期"), value=as_date(target_trade.get("trade_date"), date.today()))
                opened_date = top[1].date_input(req("开仓日期"), value=as_date(target_trade.get("opened_date"), date.today()))
                action_options = list(ACTION_LABELS.keys())
                action_value = str(target_trade.get("action") or action_options[0])
                action_index = action_options.index(action_value) if action_value in action_options else 0
                action = top[2].selectbox(req("动作"), options=action_options, index=action_index, format_func=format_action)

                strategy_options = list(STRATEGY_LABELS.keys())
                strategy_index = strategy_options.index(current_strategy) if current_strategy in strategy_options else 0
                option_strategy = top[3].selectbox(req("期权策略"), options=strategy_options, index=strategy_index, format_func=format_strategy)

                if option_strategy == "STOCK":
                    top[4].text_input("子类型", value="N/A", disabled=True)
                    sub_type = None
                else:
                    sub_type_options = list(SUB_TYPE_LABELS.keys())
                    default_sub_type = str(current_sub_type or DEFAULT_SUB_TYPE_BY_STRATEGY.get(option_strategy) or sub_type_options[0])
                    sub_type_index = sub_type_options.index(default_sub_type) if default_sub_type in sub_type_options else 0
                    sub_type = top[4].selectbox(req("子类型"), options=sub_type_options, index=sub_type_index, format_func=format_sub_type)

                # 第1行：Ticker/标的，到期日，标的价格，手续费，DTE
                r2 = st.columns(5)
                ticker = r2[0].text_input(req("Ticker / 标的"), value=str(target_trade.get("ticker") or ""))
                expiry_date = r2[1].date_input(req("到期日"), value=current_expiry)
                share_price_at_trans = r2[2].number_input("标的价格", value=float(target_trade.get("share_price_at_trans") or 0.0), step=0.01)
                fees = r2[3].number_input("手续费", value=float(target_trade.get("fees") or 0.0), step=0.01)

                # 修改页允许手动调整 DTE，默认值仍按当前记录/日期给出
                default_dte = target_trade.get("dte")
                if default_dte is None or pd.isna(default_dte):
                    default_dte = max((expiry_date - opened_date).days, 0)
                dte = r2[4].number_input("DTE", min_value=0, value=int(default_dte or 0))

                # 第2行：权利金 / 单价，执行价（最多4个）
                r3 = st.columns(5)
                premium = r3[0].number_input("权利金 / 单价", value=float(target_trade.get("premium") or 0.0), step=0.01)
                strike_labels = get_strike_labels(option_strategy)
                current_strikes = [
                    float(target_trade.get("strike_price") or 0.0),
                    float(target_trade.get("strike_price_2") or 0.0),
                    float(target_trade.get("strike_price_3") or 0.0),
                    float(target_trade.get("strike_price_4") or 0.0),
                ]
                strike_values = [None] * 4
                if option_strategy == "STOCK":
                    for i in range(1, 5):
                        r3[i].text_input("", value="", disabled=True, key=f"edit_strike_stock_empty_{i}_{target_trade_id}")
                else:
                    for idx, label in enumerate(strike_labels):
                        strike_values[idx] = r3[idx + 1].number_input(
                            req(label),
                            value=float(current_strikes[idx]),
                            step=0.5,
                            key=f"edit_form_strike_{idx+1}_{target_trade_id}",
                        )
                    for idx in range(len(strike_labels), 4):
                        r3[idx + 1].text_input("", value="", disabled=True, key=f"edit_form_strike_disabled_{idx+1}_{target_trade_id}")

                strike_price, strike_price_2, strike_price_3, strike_price_4 = strike_values

                # 第3行：Delta，Theta，IV，IV Rank，Vega
                r4 = st.columns(5)
                delta = r4[0].number_input("Delta", value=float(target_trade.get("delta") or 0.0), step=0.01)
                theta = r4[1].number_input("Theta", value=float(target_trade.get("theta") or 0.0), step=0.01)
                iv = r4[2].number_input("IV", value=float(target_trade.get("iv") or 0.0), step=0.01)
                iv_rank = r4[3].number_input("IV Rank", value=float(target_trade.get("iv_rank") or 0.0), step=0.01)
                vega = r4[4].number_input("Vega", value=float(target_trade.get("vega") or 0.0), step=0.01)
                iv_percentile = float(target_trade.get("iv_percentile") or 0.0)

                # 第4行：PoP，最大收益，最大风险，底层资产，市场
                r5 = st.columns(5)
                pop = r5[0].number_input("PoP % [盈利概率]", value=float(target_trade.get("pop") or 0.0), step=1.0)
                max_profit = r5[1].number_input("最大收益", value=float(target_trade.get("max_profit") or 0.0), step=1.0)
                max_loss = r5[2].number_input("最大风险", value=float(target_trade.get("max_loss") or 0.0), step=1.0)
                underlying_symbol = r5[3].text_input("Underlying Symbol [底层资产]", value=str(target_trade.get("underlying_symbol") or ""))
                market_options = list(MARKETS)
                market_value = str(target_trade.get("market") or market_options[0])
                market_index = market_options.index(market_value) if market_value in market_options else 0
                market = r5[4].selectbox("市场", market_options, index=market_index)

                # 第5行：保证金需求，方向，数量，乘数，策略标签
                r6 = st.columns(5)
                margin_req = r6[0].number_input("保证金需求", value=float(target_trade.get("margin_req") or 0.0), step=1.0)
                option_right_default = get_default_option_right(option_strategy)
                option_right_options = list(OPTION_RIGHTS)
                option_right_value = default_option_right if default_option_right in option_right_options else option_right_default
                option_right_index = option_right_options.index(option_right_value) if option_right_value in option_right_options else 0
                option_right = r6[1].selectbox("方向", option_right_options, index=option_right_index)
                qty = r6[2].number_input(req("数量"), min_value=1, value=int(target_trade.get("qty") or 1))
                multiplier = r6[3].number_input(req("乘数"), min_value=1, value=int(target_trade.get("multiplier") or 100))
                strategy_tag = r6[4].text_input("策略标签", value=str(target_trade.get("strategy_tag") or ""))

                position_id = str(target_trade.get("position_id") or "")
                buying_power_effect = float(target_trade.get("buying_power_effect") or 0.0)

                # 第6行：开仓理由，退出计划，滚动计划
                t1 = st.columns(3)
                entry_reason = t1[0].text_area("开仓理由", value=str(target_trade.get("entry_reason") or ""), height=100)
                exit_plan = t1[1].text_area("退出计划", value=str(target_trade.get("exit_plan") or ""), height=100)
                roll_plan = t1[2].text_area("滚动计划", value=str(target_trade.get("roll_plan") or ""), height=100)

                b1, b2, b3 = st.columns([2, 2, 8])
                submitted = b1.form_submit_button("💾 保存修改", type="primary")
                back_clicked = b2.form_submit_button("↩️ 返回交易日志显示")
                delete_clicked = b3.form_submit_button("🗑️ 删除日志", type="secondary")

                if back_clicked:
                    st.session_state["pending_nav"] = "交易日志显示"
                    st.rerun()

                if delete_clicked:
                    st.session_state.confirm_delete_trade_id = int(target_trade_id)
                    st.rerun()

                if submitted:
                    validation_errors = validate_trade_form(option_strategy, sub_type, ticker, expiry_date, strike_values)

                    if expiry_date < opened_date:
                        validation_errors.append("到期日必须大于或等于开仓日期")

                    if validation_errors:
                        for msg in validation_errors:
                            st.error(msg)
                    else:
                        try:
                            final_position_id = position_id.strip() or generate_position_id_by_trade_date(trade_date)
                        except ValueError as e:
                            st.error(str(e))
                            st.stop()
                        gross_amount = compute_gross_amount(premium, int(qty), int(multiplier))
                        net_cash_flow = compute_net_cash_flow(action, premium, int(qty), int(multiplier), fees)
                        break_even = infer_break_even(option_strategy, option_right, none_if_zero(strike_price), premium, share_price_at_trans)

                        update_payload = {
                            "trade_date": str(trade_date),
                            "opened_date": str(opened_date),
                            "position_id": final_position_id,
                            "action": action,
                            "option_strategy": option_strategy,
                            "sub_type": sub_type,
                            "strategy_tag": normalize_text(strategy_tag),
                            "ticker": ticker.strip().upper(),
                            "underlying_symbol": normalize_text(underlying_symbol),
                            "market": market,
                            "expiry_date": str(expiry_date),
                            "strike_price": none_if_zero(strike_price),
                            "strike_price_2": none_if_zero(strike_price_2),
                            "strike_price_3": none_if_zero(strike_price_3),
                            "strike_price_4": none_if_zero(strike_price_4),
                            "option_right": option_right,
                            "qty": int(qty),
                            "multiplier": int(multiplier),
                            "share_price_at_trans": none_if_zero(share_price_at_trans),
                            "premium": none_if_zero(premium),
                            "gross_amount": gross_amount,
                            "fees": safe_float(fees, 0.0),
                            "net_cash_flow": net_cash_flow,
                            "iv": none_if_zero(iv),
                            "iv_rank": none_if_zero(iv_rank),
                            "iv_percentile": none_if_zero(iv_percentile),
                            "delta": none_if_zero(delta),
                            "theta": none_if_zero(theta),
                            "vega": none_if_zero(vega),
                            "pop": none_if_zero(pop),
                            "dte": int(dte),
                            "break_even": break_even,
                            "max_profit": none_if_zero(max_profit),
                            "max_loss": none_if_zero(max_loss),
                            "margin_req": none_if_zero(margin_req),
                            "buying_power_effect": none_if_zero(buying_power_effect),
                            "entry_reason": normalize_text(entry_reason),
                            "exit_plan": normalize_text(exit_plan),
                            "roll_plan": normalize_text(roll_plan),
                        }
                        try:
                            update_trade(int(target_trade_id), update_payload)
                            st.session_state["edit_save_message"] = f"✅ 保存成功：交易记录已更新，Position ID: {final_position_id}"
                            st.session_state["edit_save_error"] = ""
                            st.session_state.edit_trade_id = int(target_trade_id)
                            try:
                                st.query_params["edit_trade_id"] = str(int(target_trade_id))
                            except Exception:
                                pass
                            st.rerun()
                        except Exception as e:
                            st.session_state["edit_save_error"] = f"❌ 保存失败：{e}"
                            st.session_state["edit_save_message"] = ""
                            st.rerun()

            # 删除确认区：必须放在 form 外，避免 st.button 在 st.form 内报错
            if st.session_state.get("confirm_delete_trade_id") == int(target_trade_id):
                delete_position_id = str(target_trade.get("position_id") or target_trade.get("id") or "")
                delete_ticker = str(target_trade.get("ticker") or "")
                delete_strategy = str(target_trade.get("option_strategy") or "")

                st.error(
                    f"确认删除日志？编号：{delete_position_id} ｜ 标的：{delete_ticker} ｜ 策略：{delete_strategy}"
                )
                confirm_col, cancel_col, _space_col = st.columns([2, 2, 8])
                if confirm_col.button("🗑️ 确认删除", key=f"confirm_delete_{int(target_trade_id)}", type="secondary"):
                    soft_delete_trade(int(target_trade_id))
                    st.session_state.last_deleted_trade_id = int(target_trade_id)
                    st.session_state.last_deleted_trade_label = f"{delete_position_id} {delete_ticker} {delete_strategy}"
                    st.session_state.confirm_delete_trade_id = None
                    st.session_state.edit_trade_id = None
                    st.session_state.selected_trade_id = None
                    st.session_state["pending_nav"] = "交易日志显示"
                    st.success("日志已删除，可在交易日志显示页撤销。")
                    st.rerun()
                if cancel_col.button("❌ 取消删除", key=f"cancel_delete_{int(target_trade_id)}"):
                    st.session_state.confirm_delete_trade_id = None
                    st.rerun()


elif page == "交易日志回填":
    st.subheader("日志回填")

    if st.session_state.get("backfill_save_message"):
        st.success(st.session_state["backfill_save_message"])
        st.session_state["backfill_save_message"] = ""
    if st.session_state.get("backfill_save_error"):
        st.error(st.session_state["backfill_save_error"])
        st.session_state["backfill_save_error"] = ""

    st.caption(":red[*] 为必填项")

    query_trade_id = None
    try:
        qp_val = st.query_params.get("trade_id")
        if qp_val:
            query_trade_id = int(qp_val)
    except Exception:
        query_trade_id = None

    target_trade_id = st.session_state.get("backfill_trade_id") or query_trade_id
    target_trade = None
    if target_trade_id:
        detail_df = get_trade_by_id(int(target_trade_id))
        if not detail_df.empty:
            candidate_trade = detail_df.iloc[0]
            if int(candidate_trade.get("is_deleted") or 0) == 0 and str(candidate_trade.get("result_status") or "").upper() != "DELETED":
                target_trade = candidate_trade
            else:
                st.session_state.backfill_trade_id = None
                try:
                    if "trade_id" in st.query_params:
                        del st.query_params["trade_id"]
                except Exception:
                    pass

    if target_trade is None:
        open_df = get_trades("result_status = :result_status", {"result_status": "OPEN"})
        if not open_df.empty and "is_deleted" in open_df.columns:
            open_df = open_df[pd.to_numeric(open_df["is_deleted"], errors="coerce").fillna(0).astype(int) == 0]
        if not open_df.empty:
            open_df = open_df.sort_values(by=["trade_date", "id"], ascending=[True, True])
            target_trade_id = int(open_df.iloc[0]["id"])
            st.session_state.backfill_trade_id = target_trade_id
            try:
                st.query_params["trade_id"] = str(target_trade_id)
            except Exception:
                pass
            target_trade = get_trade_by_id(target_trade_id).iloc[0]

    if target_trade is None:
        st.info("暂无可回填记录。")
    else:
        render_detail_cards(target_trade, show_review=False, columns=4)

        st.divider()
        st.markdown("### 回填内容")

        trade_id_int = int(target_trade["id"])
        action = str(target_trade.get("action") or "").upper()
        qty = int(target_trade.get("qty") or 1)
        multiplier = int(target_trade.get("multiplier") or 100)
        premium = float(target_trade.get("premium") or 0.0)

        close_key = f"bf_close_price_{trade_id_int}"
        pnl_key = f"bf_closed_pnl_{trade_id_int}"
        result_key = f"bf_result_status_{trade_id_int}"
        assigned_key = f"bf_assigned_{trade_id_int}"
        exec_key = f"bf_execution_score_{trade_id_int}"
        outcome_key = f"bf_outcome_type_{trade_id_int}"
        note_key = f"bf_review_note_{trade_id_int}"
        date_key = f"bf_closed_date_{trade_id_int}"

        if close_key not in st.session_state:
            st.session_state[close_key] = 0.0
        if pnl_key not in st.session_state:
            st.session_state[pnl_key] = float(target_trade.get("closed_pnl") or 0.0)

        c1, c2, c3, c4 = st.columns(4)
        closed_date = c1.date_input(req("平仓日期"), value=as_date(target_trade.get("closed_date"), date.today()), key=date_key)
        current_result_status = str(target_trade.get("result_status") or "OPEN")
        if current_result_status not in RESULT_STATUSES:
            current_result_status = "OPEN"
        result_status = c2.selectbox(req("结果状态"), RESULT_STATUSES, index=RESULT_STATUSES.index(current_result_status), key=result_key)
        assigned = c3.checkbox("是否被指派", value=bool(target_trade.get("assigned") or 0), key=assigned_key)
        execution_score = c4.selectbox(req("是否按计划执行"), EXECUTION_SCORES, index=EXECUTION_SCORES.index(str(target_trade.get("execution_score") or "YES")), key=exec_key)

        c5, c6, c7 = st.columns(3)
        close_price = c5.number_input(
            "平仓成交价格",
            min_value=0.0,
            step=0.01,
            key=close_key,
            on_change=update_backfill_pnl,
            args=(trade_id_int, action, premium, qty, multiplier),
        )
        closed_pnl = c6.number_input(req("已实现盈亏"), step=1.0, key=pnl_key)
        outcome_type = c7.selectbox(req("结果归因"), OUTCOME_TYPES, index=OUTCOME_TYPES.index(str(target_trade.get("outcome_type") or OUTCOME_TYPES[0])), key=outcome_key)

        if action == "BTO":
            st.caption("自动公式：已实现盈亏 = (平仓成交价格 - 开仓权利金) × 数量 × 乘数")
        elif action == "STO":
            st.caption("自动公式：已实现盈亏 = (开仓权利金 - 平仓成交价格) × 数量 × 乘数")
        else:
            st.caption("当前动作暂未启用自动公式，可手动填写已实现盈亏。")

        review_note = st.text_area("复盘说明", value=str(target_trade.get("review_note") or ""), height=120, key=note_key)

        btn_save, btn_back, _btn_space = st.columns([1.5, 1.8, 8])
        submitted = btn_save.button("💾 保存回填", type="primary", key=f"bf_submit_{trade_id_int}")
        if btn_back.button("📋 返回交易日志显示", key=f"bf_back_to_trade_log_{trade_id_int}", type="secondary"):
            st.session_state["pending_nav"] = "交易日志显示"
            st.rerun()

        if submitted:
            validation_errors = validate_backfill_form(closed_date, result_status, execution_score, closed_pnl, outcome_type)
            if validation_errors:
                for msg in validation_errors:
                    st.error(msg)
            else:
                open_date = pd.to_datetime(target_trade.get("opened_date") or target_trade.get("trade_date"), errors="coerce")
                close_date = pd.to_datetime(str(closed_date), errors="coerce")
                holding_days = max((close_date - open_date).days, 1) if pd.notna(open_date) and pd.notna(close_date) else 1
                capital_at_risk = float(target_trade.get("max_loss") or target_trade.get("margin_req") or 0.0)
                ann = compute_annualized_return(float(closed_pnl), capital_at_risk, holding_days)

                try:
                    update_trade(int(target_trade["id"]), {
                        "closed_date": str(close_date.date()),
                        "result_status": result_status,
                        "assigned": 1 if assigned else 0,
                        "closed_pnl": float(closed_pnl),
                        "annualized_return": ann,
                        "execution_score": execution_score,
                        "outcome_type": outcome_type,
                        "review_note": review_note.strip() or None,
                    })
                    st.session_state["backfill_save_message"] = f"✅ 回填保存成功：交易 ID {target_trade['id']}，已实现盈亏 {float(closed_pnl):.2f}"
                    st.session_state["backfill_save_error"] = ""
                    st.session_state.backfill_trade_id = int(target_trade["id"])
                    try:
                        st.query_params["trade_id"] = str(int(target_trade["id"]))
                    except Exception:
                        pass
                    st.rerun()
                except Exception as e:
                    st.session_state["backfill_save_error"] = f"❌ 回填保存失败：{e}"
                    st.session_state["backfill_save_message"] = ""
                    st.rerun()


elif page == "统计总结":
    st.subheader("统计总结")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        start_date = st.date_input("开始日期", value=date.today() - timedelta(days=365), key="summary_start")
    with col2:
        end_date = st.date_input("结束日期", value=date.today(), key="summary_end")
    with col3:
        strategy_filter = st.selectbox(
            "策略筛选",
            ["全部"] + list(STRATEGY_LABELS.keys()),
            format_func=lambda x: "全部" if x == "全部" else format_strategy(x),
            key="summary_strategy",
        )

    clauses = [
        "date(COALESCE(NULLIF(opened_date, ''), trade_date)) >= date(:start_date)",
        "date(COALESCE(NULLIF(opened_date, ''), trade_date)) <= date(:end_date)",
    ]
    params = {"start_date": str(start_date), "end_date": str(end_date)}
    if strategy_filter != "全部":
        clauses.append("option_strategy = :option_strategy")
        params["option_strategy"] = strategy_filter

    df = get_trades(" AND ".join(clauses), params)

    # 统计页统一过滤已软删除日志
    if not df.empty and "is_deleted" in df.columns:
        df = df[pd.to_numeric(df["is_deleted"], errors="coerce").fillna(0).astype(int) == 0]

    if not df.empty:
        df = df.copy()
        df["effective_open_date"] = pd.to_datetime(
            df["opened_date"].replace("", pd.NA).fillna(df["trade_date"]),
            errors="coerce",
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        df["opened_date"] = pd.to_datetime(df["opened_date"], errors="coerce")
        df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce")
        df["closed_pnl"] = pd.to_numeric(df["closed_pnl"], errors="coerce").fillna(0.0)
        df["fees"] = pd.to_numeric(df["fees"], errors="coerce").fillna(0.0)
        df["iv_rank"] = pd.to_numeric(df["iv_rank"], errors="coerce")
        df["max_loss"] = pd.to_numeric(df["max_loss"], errors="coerce")
        df["margin_req"] = pd.to_numeric(df["margin_req"], errors="coerce")
    else:
        df = pd.DataFrame()

    stats = summarize_trades(df)

    ledger_df = get_ledger()
    if not ledger_df.empty:
        ledger_df = ledger_df.copy()
        ledger_df["ledger_date"] = pd.to_datetime(ledger_df["ledger_date"], errors="coerce")
        ledger_df = ledger_df[
            (ledger_df["ledger_date"].dt.date >= start_date) &
            (ledger_df["ledger_date"].dt.date <= end_date)
        ]

    st.caption(f"按开仓日期筛选区间：{start_date} ~ {end_date}；筛选后交易数：{len(df)}")
    if not df.empty:
        debug_cols = [c for c in ["id", "ticker", "trade_date", "expiry_date", "closed_date", "option_strategy", "result_status"] if c in df.columns]
        show_df = df[debug_cols].copy()        
        show_df.rename(columns={"trade_date": "交易日期","expiry_date": "到期日期","closed_date": "平仓日期","option_strategy": "策略","result_status": "状态","ticker": "标的","id": "ID"}, inplace=True)
        show_df["交易日期"] = pd.to_datetime(show_df["交易日期"]).dt.date 
        show_df["到期日期"] = pd.to_datetime(show_df["到期日期"]).dt.date 
        show_df["平仓日期"] = pd.to_datetime(show_df["平仓日期"]).dt.date 
       
        st.dataframe(show_df, use_container_width=True, height=180)
    avg_annualized = None
    closed_trades = df[df["result_status"].isin(["WIN", "LOSS", "ASSIGNED", "CLOSED"])] if not df.empty else pd.DataFrame()

    if not closed_trades.empty and "closed_pnl" in closed_trades.columns:
        closed_trades = closed_trades.copy()
        closed_trades["open_date"] = pd.to_datetime(
            closed_trades["opened_date"].fillna(closed_trades["trade_date"]),
            errors="coerce"
        )
        closed_trades["close_date"] = pd.to_datetime(closed_trades["closed_date"], errors="coerce")
        closed_trades["holding_days"] = (closed_trades["close_date"] - closed_trades["open_date"]).dt.days
        closed_trades["holding_days"] = closed_trades["holding_days"].replace(0, 1).fillna(1)

        capital = closed_trades["max_loss"].fillna(closed_trades["margin_req"]).fillna(10000.0)
        capital = capital.replace(0, 10000.0)

        closed_trades["ann_return"] = (closed_trades["closed_pnl"] / capital) * (365 / closed_trades["holding_days"]) * 100
        avg_annualized = closed_trades["ann_return"].mean()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总交易数", int(stats.get("trade_count", 0) or 0))
    c2.metric("已平仓", int(stats.get("closed_count", 0) or 0))
    c3.metric("胜率", f"{float(stats.get('win_rate', 0) or 0):.1f}%")
    c4.metric("总已实现盈亏", f"{float(stats.get('total_closed_pnl', 0) or 0):.2f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("平均单笔已实现盈亏", f"{float(stats.get('avg_closed_pnl', 0) or 0):.2f}")
    c2.metric("总手续费", f"{float(stats.get('total_fees', 0) or 0):.2f}")
    c3.metric("平均 IV Rank", f"{float(stats.get('avg_iv_rank', 0) or 0):.2f}")
    c4.metric("平均年化收益", "-" if avg_annualized is None or pd.isna(avg_annualized) else f"{avg_annualized:.2f}%")

    if not df.empty:
        st.markdown("### 策略详细汇总")
        summary_table = df.groupby("option_strategy").agg({
            "id": "count",
            "closed_pnl": ["sum", "mean"],
            "result_status": lambda x: (x == "WIN").mean() * 100 if len(x) else 0
        }).round(2)
        summary_table.columns = ["交易次数", "总盈亏", "平均盈亏", "胜率(%)"]
        st.dataframe(summary_table, use_container_width=True)

        work = display_trade_df(df)

        st.markdown("### 按策略/子类型汇总已实现盈亏")
        strategy_pnl = work.groupby(["option_strategy", "sub_type"], dropna=False)["closed_pnl"].sum(min_count=1).reset_index()
        if not strategy_pnl.empty:
            strategy_pnl["sub_type"] = strategy_pnl["sub_type"].fillna("")
            strategy_pnl["closed_pnl"] = pd.to_numeric(strategy_pnl["closed_pnl"], errors="coerce").fillna(0.0)
            strategy_pnl["label"] = strategy_pnl.apply(
                lambda r: f"{r['option_strategy']} ({r['sub_type']})" if r["sub_type"] else r["option_strategy"],
                axis=1
            )
            strategy_pnl["color"] = strategy_pnl["closed_pnl"].apply(
                lambda x: "#22c55e" if x > 0 else "#ef4444" if x < 0 else "#6b7280"
            )

            fig1 = px.bar(
                strategy_pnl, x="label", y="closed_pnl", title="按策略/子类型汇总已实现盈亏",
                color="color", color_discrete_map={"#22c55e": "green", "#ef4444": "red", "#6b7280": "gray"}
            )
            fig1.update_traces(marker_line_color="white", marker_line_width=0.5)
            fig1.update_layout(showlegend=False, xaxis_title="策略 / 子类型", yaxis_title="已实现盈亏 (USD)")
            st.plotly_chart(fig1, use_container_width=True)

        st.markdown("### 按标的统计")
        ticker_pnl = work.groupby("ticker")["closed_pnl"].sum(min_count=1).reset_index()
        if not ticker_pnl.empty:
            ticker_pnl["closed_pnl"] = pd.to_numeric(ticker_pnl["closed_pnl"], errors="coerce").fillna(0.0)
            ticker_pnl["color"] = ticker_pnl["closed_pnl"].apply(
                lambda x: "#22c55e" if x > 0 else "#ef4444" if x < 0 else "#6b7280"
            )

            fig2 = px.bar(
                ticker_pnl, x="ticker", y="closed_pnl", title="按标的汇总已实现盈亏",
                color="color", color_discrete_map={"#22c55e": "green", "#ef4444": "red", "#6b7280": "gray"}
            )
            fig2.update_traces(marker_line_color="white", marker_line_width=0.5)
            fig2.update_layout(showlegend=False, xaxis_title="标的", yaxis_title="已实现盈亏 (USD)")
            st.plotly_chart(fig2, use_container_width=True)

    st.markdown("### 资金 / 权益曲线")
    curve_df = build_equity_curve(df, ledger_df)
    if curve_df.empty:
        st.info("暂无可绘制的数据")
    else:
        renderLightweightCharts(build_lightweight_series(curve_df), key="equity_curve")
        st.dataframe(curve_df, use_container_width=True)
