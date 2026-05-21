from __future__ import annotations

from datetime import date, timedelta, datetime
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

COMBO_STRATEGIES = {"COLLAR", "SYNTHETIC_SHORT", "SYNTHETIC_LONG"}

# ====================== 顶部导航跳转处理 ======================
if "pending_nav" in st.session_state:
    st.session_state["nav_page"] = st.session_state["pending_nav"]
    del st.session_state["pending_nav"]

nav_options = ["录入交易日志", "交易日志显示", "修改交易日志", "交易日志回填", "日志回收站", "资金流水录入", "资金流水查看", "统计总结", "常用链接"]

if "nav_page" not in st.session_state or st.session_state["nav_page"] not in nav_options:
    st.session_state["nav_page"] = "交易日志显示"

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
if "show_related_contract_dialog_trade_id" not in st.session_state:
    st.session_state.show_related_contract_dialog_trade_id = None


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


def format_execution_symbol(row) -> str:
    """日志列表用简短腿型显示执行方向：+C/-C/+P/-P。

    仅用于展示，不改变数据库字段和任何盈亏计算。
    """
    strategy = str(row.get("option_strategy") or "").upper()
    action = str(row.get("action") or "").upper()

    if strategy == "CALL":
        return "+C" if action == "BTO" else "-C"
    if strategy in ["PUT", "CSP"]:
        return "+P" if action == "BTO" else "-P"
    if strategy in ["SYNTHETIC_SHORT", "COLLAR"]:
        return "+P -C"
    if strategy == "SYNTHETIC_LONG":
        return "+C -P"
    if strategy == "CALL_SPREAD":
        return "+C -C"
    if strategy == "PUT_SPREAD":
        return "+P -P"
    if strategy == "IRON_CONDOR":
        return "+P -P -C +C"
    if strategy == "STRANGLE":
        return "-P -C"
    if strategy == "CC":
        return "-C"
    if strategy == "STOCK":
        return "买股" if action == "BUY_SHARES" else "卖股"
    return action


def get_strike_leg_symbols(row) -> list[str]:
    """合约信息卡片里给执行价加腿型前缀，如 +P/-C。

    仅用于展示，不改变数据库字段和任何盈亏计算。
    """
    strategy = str(row.get("option_strategy") or "").upper()
    action = str(row.get("action") or "").upper()

    if strategy == "CALL":
        return ["+C" if action == "BTO" else "-C"]
    if strategy in ["PUT", "CSP"]:
        return ["+P" if action == "BTO" else "-P"]
    if strategy in ["SYNTHETIC_SHORT", "COLLAR"]:
        return ["+P", "-C"]
    if strategy == "SYNTHETIC_LONG":
        return ["+C", "-P"]
    if strategy == "CALL_SPREAD":
        return ["+C", "-C"]
    if strategy == "PUT_SPREAD":
        return ["+P", "-P"]
    if strategy == "IRON_CONDOR":
        return ["+P", "-P", "-C", "+C"]
    if strategy == "STRANGLE":
        return ["-P", "-C"]
    if strategy == "CC":
        return ["-C"]
    return []


def display_trade_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["action_display"] = out["action"].fillna("").map(lambda x: f"{x} - {ACTION_LABELS.get(x, x)}")
    out["execution_display"] = out.apply(format_execution_symbol, axis=1)
    out["strategy_display"] = out["option_strategy"].fillna("").map(lambda x: STRATEGY_LABELS.get(x, x) if x else "")
    out["sub_type_display"] = out["sub_type"].fillna("").map(lambda x: str(x) if x else "")
    return out


def display_ledger_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "ledger_type" in out.columns:
        out["ledger_type_display"] = out["ledger_type"].fillna("").map(format_ledger_type)
    return out


def get_sub_type_options_for_strategy(strategy: str) -> list[str]:
    """根据策略限制子类型，避免录入时选到明显不匹配的 Credit/Debit。"""
    strategy = (strategy or "").upper()
    if strategy == "STOCK":
        return []
    if strategy in ["CSP", "CC", "IRON_CONDOR", "STRANGLE"]:
        return ["CREDIT"]
    if strategy in ["CALL", "PUT", "CALL_SPREAD", "PUT_SPREAD", "COLLAR", "SYNTHETIC_SHORT", "SYNTHETIC_LONG"]:
        return ["DEBIT", "CREDIT"]
    return list(SUB_TYPE_LABELS.keys())


def get_action_options_for_strategy(strategy: str, sub_type: str | None = None) -> list[str]:
    """根据策略和子类型限制动作。

    说明：组合策略没有新增数据库字段，仍用 BTO/STO 表示组合净现金流方向：
    - DEBIT -> BTO，代表净付权利金
    - CREDIT -> STO，代表净收权利金
    这样可以复用原 compute_net_cash_flow 逻辑。
    """
    strategy = (strategy or "").upper()
    sub_type = (sub_type or "").upper()

    if strategy == "STOCK":
        return ["BUY_SHARES", "SELL_SHARES"]
    if strategy in ["CSP", "CC", "IRON_CONDOR", "STRANGLE"]:
        return ["STO"]
    if strategy in ["CALL", "PUT", "CALL_SPREAD", "PUT_SPREAD", "COLLAR", "SYNTHETIC_SHORT", "SYNTHETIC_LONG"]:
        return ["STO"] if sub_type == "CREDIT" else ["BTO"]
    return list(ACTION_LABELS.keys())


def get_default_option_right(strategy: str) -> str:
    if strategy == "STOCK": return "NONE"
    if strategy in ["PUT", "CSP", "PUT_SPREAD"]: return "PUT"
    if strategy in ["CALL", "CC", "CALL_SPREAD"]: return "CALL"
    if strategy in ["COLLAR", "SYNTHETIC_SHORT", "SYNTHETIC_LONG"]: return "BOTH"
    return "BOTH"


def get_strike_labels(strategy: str) -> list[str]:
    if strategy == "CALL": return ["Call Strike Price"]
    if strategy == "PUT": return ["Put Strike Price"]
    if strategy == "CALL_SPREAD": return ["Long Call Price", "Short Call Price"]
    if strategy == "PUT_SPREAD": return ["Long Put Price", "Short Put Price"]
    if strategy == "IRON_CONDOR": return ["Long Put Price", "Short Put Price", "Short Call Price", "Long Call Price"]
    if strategy == "STRANGLE": return ["Short Put Price", "Short Call Price"]
    if strategy == "COLLAR": return ["Long Put Strike Price", "Short Call Strike Price"]
    if strategy == "SYNTHETIC_SHORT": return ["Long Put Strike Price", "Short Call Strike Price"]
    if strategy == "SYNTHETIC_LONG": return ["Long Call Strike Price", "Short Put Strike Price"]
    if strategy == "CSP": return ["Short Put Price"]
    if strategy == "CC": return ["Short Call Price"]
    if strategy == "STOCK": return []
    return ["Strike Price"]


def validate_trade_form(option_strategy: str, sub_type, ticker: str, expiry_date, strike_values):
    errors = []
    if not (option_strategy or "").strip(): errors.append("期权策略 为必填项")
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
        "action_display": "动作", "execution_display": "执行", "strategy_display": "策略", "sub_type_display": "子类型",
        "expiry_date": "到期日", "strike_price": "执行价1", "strike_price_2": "执行价2",
        "strike_price_3": "执行价3", "strike_price_4": "执行价4", "qty": "数量",
        "premium": "权利金", "fees": "手续费", "net_cash_flow": "净现金流",
        "result_status": "状态", "closed_pnl": "已实现盈亏", "closed_date": "平仓日期",
    }
    return mapping.get(col, col)


def render_detail_cards(detail_row: pd.Series, show_review: bool = True, columns: int = 3):
    if detail_row is None or detail_row.empty:
        return

    # def val(field: str, default: str = ""):
    #     v = detail_row.get(field, default)
    #     return "" if pd.isna(v) else str(v)
    def val(field: str, default: str = ""):
        v = detail_row.get(field, default)
        if pd.isna(v):
            return ""

        # 金额类字段：固定显示 2 位小数
        money_fields = {
            "premium",
            "gross_amount",
            "fees",
            "net_cash_flow",
            "closed_pnl",
            "max_profit",
            "max_loss",
            "margin_req",
            "buying_power_effect",
            "break_even",
            "share_price_at_trans",
            "strike_price",
            "strike_price_2",
            "strike_price_3",
            "strike_price_4"
        }

        if field in money_fields:
            try:
                return f"{float(v):.2f}"
            except Exception:
                return str(v)
        return str(v)



    strike_leg_symbols = get_strike_leg_symbols(detail_row)

    def strike_val(field: str, index: int) -> str:
        value = val(field)
        if not value:
            return ""
        symbol = strike_leg_symbols[index] if index < len(strike_leg_symbols) else ""
        return f"{symbol} {value}".strip()

    def assigned_display() -> str:
        """复盘信息中的是否被指派：0/空不显示，1 显示“是”。"""
        v = detail_row.get("assigned", None)
        if pd.isna(v):
            return ""
        try:
            return "是" if int(float(v)) == 1 else ""
        except Exception:
            return "是" if str(v).strip().lower() in {"1", "true", "yes", "是"} else ""

    groups = {
        "基本信息": [
            ("标的", val("ticker")),
            ("编号", val("id")), ("持仓ID", val("position_id")), ("交易日期", val("trade_date")),
            ("开仓日期", val("opened_date")), ("市场", val("market")),
            ("动作", f"{val('action')} - {ACTION_LABELS.get(val('action'), val('action'))}" if val("action") else ""),
            ("策略", f"{val('option_strategy')} - {STRATEGY_LABELS.get(val('option_strategy'), val('option_strategy'))}" if val("option_strategy") else ""),
        ],
        "合约信息": [
            ("子类型", f"{val('sub_type')} - {SUB_TYPE_LABELS.get(val('sub_type'), val('sub_type'))}" if val("sub_type") else ""),
            ("到期日", val("expiry_date")),
            ("执行价1", strike_val("strike_price", 0)), ("执行价2", strike_val("strike_price_2", 1)),
            ("执行价3", strike_val("strike_price_3", 2)), ("执行价4", strike_val("strike_price_4", 3)),
            ("方向", val("option_right")), ("DTE", val("dte")),
            ("关联合约", format_related_trade_text(int(detail_row.get("id")))),
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
            ("是否被指派", assigned_display()), ("执行评分", val("execution_score")),
            ("结果归因", val("outcome_type")), ("复盘说明", val("review_note")),
        ]

    #st.markdown("### 详细信息")
    grid_class = "detail-card-grid-4" if columns == 4 else "detail-card-grid-3"

    cards_html = []
    for title, items in groups.items():
        shown = [(k, v) for k, v in items if str(v).strip() or k == "关联合约"]
        if not shown: continue
        # rows_html = "".join(
        #     f'<div class="detail-card-row"><div class="detail-card-label">{html.escape(k)}</div>'
        #     f'<div class="detail-card-value">{html.escape(v)}</div></div>'
        #     for k, v in shown
        # )
        rows_html_parts = []
        current_trade_id = int(detail_row.get("id"))

        for k, v in shown:
            value_style = ""

            if k == "净现金流":
                try:
                    num_value = float(str(v).replace(",", ""))
                    if num_value < 0:
                        value_style = "color:#16a34a;"
                except Exception:
                    pass

            if k == "已实现盈亏":
                try:
                    num_value = float(str(v).replace(",", ""))
                    if num_value > 0:
                        value_style = "color:#dc2626;font-weight:700;"
                    elif num_value < 0:
                        value_style = "color:#16a34a;font-weight:700;"
                except Exception:
                    pass

            label_html = html.escape(k)
            value_html = html.escape(str(v))

            # 关联合约末尾的【现金流合计】如果为负数，只把数字显示为绿色加粗
            if k == "关联合约":
                raw_value = str(v)
                if "【" in raw_value and "】" in raw_value:
                    prefix, rest = raw_value.rsplit("【", 1)
                    cash_text, suffix = rest.split("】", 1)
                    try:
                        cash_num = float(cash_text.replace(",", ""))
                        if cash_num < 0:
                            value_html = (
                                f'{html.escape(prefix)}【'
                                f'<span style="color:#16a34a;font-weight:700;">{html.escape(cash_text)}</span>'
                                f'】{html.escape(suffix)}'
                            )
                    except Exception:
                        value_html = html.escape(raw_value)

            rows_html_parts.append(
                f'<div class="detail-card-row">'
                f'<div class="detail-card-label">{label_html}</div>'
                f'<div class="detail-card-value" style="{value_style}">{value_html}</div>'
                f'</div>'
            )

        rows_html = "".join(rows_html_parts)
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


@st.dialog("管理关联合约")
def related_contract_dialog(trade_id: int):
    ensure_related_trade_column()
    st.write(f"用于自定义多腿策略时关联相关合约")
    current_df = get_trade_by_id(int(trade_id))
    if current_df.empty:
        st.warning("未找到当前交易。")
        return

    current = current_df.iloc[0]
    ticker = str(current.get("ticker") or "")
    position_id = str(current.get("position_id") or current.get("id"))

    st.write(f"当前合约：**{position_id} / {ticker}**")

    # 显示当前已关联的合约信息
    current_related_text = format_related_trade_text(int(trade_id))
    if not str(current_related_text).strip():
        st.info("当前已关联：未关联")
    else:
        st.info(f"当前已关联：{current_related_text}")

    candidates = get_related_candidates(int(trade_id))

    if candidates.empty:
        st.info("暂无可关联的同标的 OPEN 合约。")
        if st.button("关闭", use_container_width=True):
            st.session_state.show_related_contract_dialog_trade_id = None
            try:
                if "manage_related_trade_id" in st.query_params:
                    del st.query_params["manage_related_trade_id"]
            except Exception:
                pass
            st.rerun()
        return

    current_related_id = None
    if "related_trade_id" in current.index and not pd.isna(current.get("related_trade_id")):
        try:
            current_related_id = int(current.get("related_trade_id"))
        except Exception:
            current_related_id = None

    # 兼容旧版单向关联：如果当前记录没有 related_trade_id，
    # 但其他合约指向了当前记录，也把它识别为当前已关联对象。
    if current_related_id is None:
        related_row = get_related_trade_row(int(trade_id))
        if related_row and related_row["id"] is not None:
            try:
                current_related_id = int(related_row["id"])
            except Exception:
                current_related_id = None

    option_ids = candidates["id"].astype(int).tolist()

    def candidate_label(row):
        pid = row.get("position_id") or row.get("id")
        strategy = str(row.get("option_strategy") or "")
        status = row.get("result_status") or ""
        qty = row.get("qty") or ""
        strike_1 = row.get("strike_price") or ""

        try:
            strike_1 = f"{float(strike_1):.2f}" if strike_1 != "" else ""
        except Exception:
            strike_1 = str(strike_1)

        return f"{pid} - {strategy} - {status} - {qty} - {strike_1}"

    labels = {
        int(row["id"]): candidate_label(row)
        for _, row in candidates.iterrows()
    }

    default_index = 0
    if current_related_id in option_ids:
        default_index = option_ids.index(current_related_id)

    selected_related_id = st.selectbox(
        "选择要关联的原有合约",
        options=option_ids,
        index=default_index,
        format_func=lambda x: labels.get(int(x), str(x)),
    )

    col1, col2, col3 = st.columns(3)

    if col1.button("✅ 保存关联", type="primary", use_container_width=True):
        set_related_trade(int(trade_id), int(selected_related_id))
        st.session_state.show_related_contract_dialog_trade_id = None
        try:
            if "manage_related_trade_id" in st.query_params:
                del st.query_params["manage_related_trade_id"]
        except Exception:
            pass
        st.success("已保存关联合约。")
        st.rerun()

    if col2.button("🗑️ 删除关联", use_container_width=True):
        set_related_trade(int(trade_id), None)
        st.session_state.show_related_contract_dialog_trade_id = None
        try:
            if "manage_related_trade_id" in st.query_params:
                del st.query_params["manage_related_trade_id"]
        except Exception:
            pass
        st.success("已删除关联合约。")
        st.rerun()

    if col3.button("取消", use_container_width=True):
        st.session_state.show_related_contract_dialog_trade_id = None
        try:
            if "manage_related_trade_id" in st.query_params:
                del st.query_params["manage_related_trade_id"]
        except Exception:
            pass
        st.rerun()


def render_related_contract_link_button(trade_id: int):
    """用 Streamlit 原生按钮打开关联合约弹窗。样式做成文字链接，避免 HTML 链接无法触发 Python 回调。"""
    st.markdown(
        """
        <style>
        div[data-testid="stButton"] button[key^="manage_related_contract_btn_"] {
            background: transparent !important;
            border: none !important;
            color: #2563eb !important;
            text-decoration: underline !important;
            padding: 0 !important;
            min-height: 0 !important;
            font-size: 14px !important;
            font-weight: 400 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if st.button("🔗 管理关联合约", key=f"manage_related_contract_btn_{int(trade_id)}"):
        st.session_state.show_related_contract_dialog_trade_id = int(trade_id)
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


def ensure_trade_indexes():
    """为交易日志列表常用筛选字段和关联合约字段创建索引。"""
    ensure_related_trade_column()
    with _sqlite_connect() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_result_status ON trades(result_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_trade_date ON trades(trade_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_option_strategy ON trades(option_strategy)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_sub_type ON trades(sub_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_related_trade_id ON trades(related_trade_id)")
        conn.commit()



def ensure_related_trade_column():
    """为关联合约准备字段；已存在则忽略。"""
    with _sqlite_connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "related_trade_id" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN related_trade_id INTEGER")
        conn.commit()


def set_related_trade(trade_id: int, related_trade_id):
    """
    设置或清除关联合约。

    规则：
    - A 关联 B 后，同时写入 A -> B 和 B -> A。
    - A 重新绑定 C 时，自动解除 A 原来的对手方，以及 C 原来的对手方。
    - A 删除关联时，如果 B 正好关联 A，也同步清空 B。
    """
    ensure_related_trade_column()
    trade_id = int(trade_id)
    related_trade_id = int(related_trade_id) if related_trade_id is not None else None

    if related_trade_id == trade_id:
        return

    with _sqlite_connect() as conn:
        current = conn.execute(
            "SELECT related_trade_id FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        old_related_id = None
        if current and current["related_trade_id"] is not None:
            old_related_id = int(current["related_trade_id"])

        # 先解除当前合约原来的对手方。
        if old_related_id is not None:
            conn.execute(
                "UPDATE trades SET related_trade_id = NULL WHERE id = ? AND related_trade_id = ?",
                (old_related_id, trade_id),
            )

        # 如果只是删除关联，清空当前合约即可。
        if related_trade_id is None:
            conn.execute(
                "UPDATE trades SET related_trade_id = NULL WHERE id = ?",
                (trade_id,),
            )
            conn.commit()
            return

        # 如果目标合约原本关联了别人，也先解除那一边。
        target = conn.execute(
            "SELECT related_trade_id FROM trades WHERE id = ?",
            (related_trade_id,),
        ).fetchone()
        target_old_related_id = None
        if target and target["related_trade_id"] is not None:
            target_old_related_id = int(target["related_trade_id"])

        if target_old_related_id is not None and target_old_related_id != trade_id:
            conn.execute(
                "UPDATE trades SET related_trade_id = NULL WHERE id = ? AND related_trade_id = ?",
                (target_old_related_id, related_trade_id),
            )

        # 写入双向绑定。
        conn.execute(
            "UPDATE trades SET related_trade_id = ? WHERE id = ?",
            (related_trade_id, trade_id),
        )
        conn.execute(
            "UPDATE trades SET related_trade_id = ? WHERE id = ?",
            (trade_id, related_trade_id),
        )
        conn.commit()


def get_related_trade_row(trade_id: int):
    """读取当前交易关联的合约记录；兼容旧版单向关联数据。"""
    ensure_related_trade_column()
    with _sqlite_connect() as conn:
        row = conn.execute(
            """
            SELECT r.*
            FROM trades t
            LEFT JOIN trades r ON r.id = t.related_trade_id
            WHERE t.id = ?
            """,
            (int(trade_id),),
        ).fetchone()

        if row and row["id"] is not None:
            return row

        # 兼容旧数据：如果别人指向当前合约，也认为存在关联。
        return conn.execute(
            """
            SELECT *
            FROM trades
            WHERE related_trade_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(trade_id),),
        ).fetchone()


def has_related_trade(trade_id: int) -> bool:
    """判断交易是否有关联合约；兼容旧版单向关联数据。"""
    row = get_related_trade_row(int(trade_id))
    return bool(row and row["id"] is not None)


def get_related_trade_icon(trade_id: int) -> str:
    """关联合约统一使用同一个图标。"""
    row = get_related_trade_row(int(trade_id))
    if not row or row["id"] is None:
        return ""
    return "🔗"


def get_related_trade_color(trade_id: int) -> str:
    """
    为不同关联对返回不同颜色。
    同一对合约用同一个颜色；兼容旧版单向关联数据。
    """
    row = get_related_trade_row(int(trade_id))
    if not row or row["id"] is None:
        return ""

    current_id = int(trade_id)
    related_id = int(row["id"])
    pair_a, pair_b = sorted([current_id, related_id])

    colors = [
        "#2563eb",  # blue
        "#16a34a",  # green
        "#dc2626",  # red
        "#9333ea",  # purple
        "#ea580c",  # orange
        "#0891b2",  # cyan
        "#be123c",  # rose
        "#4f46e5",  # indigo
        "#65a30d",  # lime
        "#b45309",  # amber
    ]
    color_index = (pair_a * 31 + pair_b * 17) % len(colors)
    return colors[color_index]


def get_related_pair_color(trade_id: int, related_id) -> str:
    """根据当前记录ID和关联记录ID返回稳定颜色；避免列表逐行查询数据库。"""
    try:
        current_id = int(trade_id)
        related_id = int(related_id)
    except Exception:
        return ""

    pair_a, pair_b = sorted([current_id, related_id])
    colors = [
        "#2563eb",  # blue
        "#16a34a",  # green
        "#dc2626",  # red
        "#9333ea",  # purple
        "#ea580c",  # orange
        "#0891b2",  # cyan
        "#be123c",  # rose
        "#4f46e5",  # indigo
        "#65a30d",  # lime
        "#b45309",  # amber
    ]
    color_index = (pair_a * 31 + pair_b * 17) % len(colors)
    return colors[color_index]


def get_related_trade_icon_html(trade_id: int, related_id=None) -> str:
    """返回可变色的关联合约图标 HTML。related_id 存在时不再查询数据库。"""
    color = get_related_pair_color(int(trade_id), related_id) if related_id is not None else get_related_trade_color(int(trade_id))
    if not color:
        return ""

    safe_color = html.escape(color)
    return (
        f'<span style="display:inline-flex;vertical-align:-2px;color:{safe_color};margin-left:4px;">'
        f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg" aria-label="关联合约">'
        f'<path d="M10.6 13.4a1.8 1.8 0 0 0 2.8 0l3.7-3.7a3.2 3.2 0 0 0-4.5-4.5l-1.1 1.1" '
        f'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M13.4 10.6a1.8 1.8 0 0 0-2.8 0l-3.7 3.7a3.2 3.2 0 0 0 4.5 4.5l1.1-1.1" '
        f'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
        f'</span>'
    )


def add_related_info_to_df(df: pd.DataFrame) -> pd.DataFrame:
    """一次性用 SQL 给交易列表补充 has_related / related_id，避免每行查询数据库。"""
    if df.empty or "id" not in df.columns:
        return df

    ensure_related_trade_column()
    out = df.copy()
    ids = [int(x) for x in out["id"].dropna().astype(int).tolist()]
    if not ids:
        out["has_related"] = 0
        out["related_id"] = None
        return out

    placeholders = ",".join(["?"] * len(ids))
    sql = f"""
        SELECT
            t.id AS id,
            CASE
                WHEN r1.id IS NOT NULL OR r2.id IS NOT NULL THEN 1
                ELSE 0
            END AS has_related,
            COALESCE(r1.id, r2.id) AS related_id
        FROM trades t
        LEFT JOIN trades r1 ON r1.id = t.related_trade_id
        LEFT JOIN trades r2 ON r2.related_trade_id = t.id
        WHERE t.id IN ({placeholders})
    """

    with _sqlite_connect() as conn:
        related_df = pd.read_sql_query(sql, conn, params=ids)

    if related_df.empty:
        out["has_related"] = 0
        out["related_id"] = None
        return out

    return out.merge(related_df, on="id", how="left")


def format_related_trade_text(trade_id: int) -> str:
    """
    显示格式：合约持仓ID - 策略 - 状态 - 数量 - 行权价1 - 两合约现金流合计
    """
    row = get_related_trade_row(int(trade_id))
    if not row or row["id"] is None:
        return ""

    position_id = row["position_id"] or row["id"]
    strategy = str(row["option_strategy"] or "")
    status = row["result_status"] or ""
    qty = row["qty"] or ""
    strike_1 = row["strike_price"] or ""

    try:
        strike_1 = f"{float(strike_1):.2f}" if strike_1 != "" else ""
    except Exception:
        strike_1 = str(strike_1)

    def _cash_value(value) -> float:
        try:
            if value is None or pd.isna(value):
                return 0.0
            return float(value)
        except Exception:
            return 0.0

    current_cash_flow = 0.0
    current_df = get_trade_by_id(int(trade_id))
    if not current_df.empty:
        current_cash_flow = _cash_value(current_df.iloc[0].get("net_cash_flow"))

    related_cash_flow = _cash_value(row["net_cash_flow"] if "net_cash_flow" in row.keys() else None)
    cash_flow_sum = current_cash_flow + related_cash_flow

    return f"{position_id} - {strategy} - {status} - {qty} - {strike_1} - 【{cash_flow_sum:.2f}】"


def get_related_candidates(current_trade_id: int) -> pd.DataFrame:
    """获取同标的 OPEN 状态原有合约，排除当前合约和已删除合约。"""
    current_df = get_trade_by_id(int(current_trade_id))
    if current_df.empty:
        return pd.DataFrame()

    current = current_df.iloc[0]
    ticker = str(current.get("ticker") or "").strip().upper()
    if not ticker:
        return pd.DataFrame()

    df = get_trades(
        "ticker = :ticker AND result_status = :result_status",
        {"ticker": ticker, "result_status": "OPEN"},
    )

    if df.empty:
        return df

    if "is_deleted" in df.columns:
        df = df[pd.to_numeric(df["is_deleted"], errors="coerce").fillna(0).astype(int) == 0]

    df = df[df["id"].astype(int) != int(current_trade_id)]
    return df


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




def ensure_common_links_table():
    """创建常用链接表；只在不存在时创建，不影响其它数据表。"""
    with _sqlite_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS common_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                url TEXT NOT NULL,
                description TEXT
            )
            """
        )
        conn.commit()


def insert_common_link(url: str, description: str):
    ensure_common_links_table()
    url = (url or "").strip()
    description = (description or "").strip()
    if not url:
        return
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    with _sqlite_connect() as conn:
        conn.execute(
            "INSERT INTO common_links (created_at, url, description) VALUES (?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url, description),
        )
        conn.commit()


def delete_common_link(link_id: int):
    ensure_common_links_table()
    with _sqlite_connect() as conn:
        conn.execute("DELETE FROM common_links WHERE id = ?", (int(link_id),))
        conn.commit()


def get_common_links() -> pd.DataFrame:
    ensure_common_links_table()
    with _sqlite_connect() as conn:
        return pd.read_sql_query(
            "SELECT id, created_at, url, description FROM common_links ORDER BY created_at DESC, id DESC",
            conn,
        )


def format_common_link_seq(created_at: str, link_id: int) -> str:
    dt = pd.to_datetime(created_at, errors="coerce")
    if pd.isna(dt):
        return str(link_id)
    return dt.strftime("%Y%m%d%H%M")

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

    # 先选策略，再根据策略限制动作和子类型；顶部顺序：期权策略 -> 动作 -> 子类型。
    # 新增日志时默认显示空选项，避免误选第一项策略。
    strategy_options = [""] + list(STRATEGY_LABELS.keys())
    option_strategy = top[2].selectbox(
        req("期权策略"),
        options=strategy_options,
        format_func=lambda x: "请选择期权策略" if x == "" else format_strategy(x),
        key=f"top_option_strategy_{trade_form_nonce}",
    )

    default_sub_type = DEFAULT_SUB_TYPE_BY_STRATEGY.get(option_strategy)
    if not option_strategy:
        action = top[3].selectbox(
            req("动作"),
            options=[""],
            format_func=lambda x: "请先选择期权策略",
            disabled=True,
            key=f"top_action_{trade_form_nonce}_EMPTY",
        )
        sub_type = None
        top[4].text_input(req("子类型"), value="请先选择期权策略", disabled=True, key=f"top_sub_type_empty_{trade_form_nonce}")
    elif option_strategy == "STOCK":
        sub_type = None
        action_options = get_action_options_for_strategy(option_strategy, sub_type)
        action = top[3].selectbox(
            req("动作"),
            options=action_options,
            format_func=format_action,
            key=f"top_action_{trade_form_nonce}_{option_strategy}_NA",
        )
        top[4].text_input("子类型", value="N/A", disabled=True, key=f"top_sub_type_na_{trade_form_nonce}")
    else:
        sub_type_options = get_sub_type_options_for_strategy(option_strategy)
        default_index = sub_type_options.index(default_sub_type) if default_sub_type in sub_type_options else 0
        sub_type = top[4].selectbox(
            req("子类型"),
            options=sub_type_options,
            index=default_index,
            format_func=format_sub_type,
            key=f"top_sub_type_{trade_form_nonce}_{option_strategy}",
        )

        action_options = get_action_options_for_strategy(option_strategy, sub_type)
        action = top[3].selectbox(
            req("动作"),
            options=action_options,
            format_func=format_action,
            key=f"top_action_{trade_form_nonce}_{option_strategy}_{sub_type or 'NA'}",
        )

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
        if not option_strategy:
            for i in range(1, 5):
                r3[i].text_input("", value="", disabled=True, key=f"strike_strategy_empty_{i}_{trade_form_nonce}")
        elif option_strategy == "STOCK":
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

        # 方向由期权策略自动确定，避免手动选择导致后续盈亏平衡等计算误判。
        option_right = get_default_option_right(option_strategy) if option_strategy else ""
        r6[1].text_input("方向", value=option_right, disabled=True)

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

                    # 金额类字段统一显示 2 位小数
                    money_cols = {"net_cash_flow", "premium", "fees", "closed_pnl","strike_price","strike_price_2","strike_price_3","strike_price_4"}
                    if col in money_cols and value != "":
                      try:
                         display_value = f"{float(value):.2f}"
                      except Exception:
                         display_value = str(value)
                    else:
                         display_value = str(value)

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



elif page == "常用链接":
    st.subheader("常用链接")
    ensure_common_links_table()

    if "selected_common_link_id" not in st.session_state:
        st.session_state.selected_common_link_id = None
    if "common_link_form_nonce" not in st.session_state:
        st.session_state.common_link_form_nonce = 0
    if st.session_state.get("pending_clear_common_link_form", False):
        st.session_state.common_link_form_nonce += 1
        st.session_state.pending_clear_common_link_form = False

    common_link_form_nonce = st.session_state.common_link_form_nonce

    input_cols = st.columns([5, 2.2, 1.2, 1.2])
    link_url = input_cols[0].text_input("链接", key=f"common_link_url_{common_link_form_nonce}")
    link_desc = input_cols[1].text_input("说明", key=f"common_link_desc_{common_link_form_nonce}")
    # 用自定义 label，避免 st.markdown 默认段落边距把按钮挤得偏下
    input_cols[2].markdown(
        "<div style='height:26px; line-height:26px; font-weight:400; font-size:14px;'>操作</div>",
        unsafe_allow_html=True,
    )
    input_cols[3].markdown(
        "<div style='height:26px; line-height:26px;'>&nbsp;</div>",
        unsafe_allow_html=True,
    )

    if input_cols[2].button("增加", type="primary", use_container_width=True):
        if not str(link_url).strip():
            st.warning("请先输入链接。")
        else:
            insert_common_link(link_url, link_desc)
            st.session_state.selected_common_link_id = None
            st.session_state.pending_clear_common_link_form = True
            st.success("链接已增加。")
            st.rerun()

    delete_disabled = st.session_state.selected_common_link_id is None
    if input_cols[3].button("删除", disabled=delete_disabled, use_container_width=True):
        delete_common_link(int(st.session_state.selected_common_link_id))
        st.session_state.selected_common_link_id = None
        st.success("链接已删除。")
        st.rerun()

    links_df = get_common_links()
    if links_df.empty:
        st.info("暂无常用链接。")
    else:
        header_cols = st.columns([0.7, 2.2, 6.0, 1.5])
        header_cols[0].markdown('<div class="trade-header">选择</div>', unsafe_allow_html=True)
        header_cols[1].markdown('<div class="trade-header">说明</div>', unsafe_allow_html=True)
        header_cols[2].markdown('<div class="trade-header">链接</div>', unsafe_allow_html=True)
        header_cols[3].markdown('<div class="trade-header">日期</div>', unsafe_allow_html=True)

        link_ids = links_df["id"].astype(int).tolist()
        current_selected = st.session_state.selected_common_link_id
        if current_selected not in link_ids:
            st.session_state.selected_common_link_id = None
            current_selected = None

        def handle_common_link_select(link_id: int, all_link_ids: list[int]):
            key = f"common_link_select_{link_id}"
            checked = bool(st.session_state.get(key, False))
            if checked:
                st.session_state.selected_common_link_id = int(link_id)
                for other_id in all_link_ids:
                    st.session_state[f"common_link_select_{other_id}"] = (int(other_id) == int(link_id))
            else:
                if st.session_state.get("selected_common_link_id") == int(link_id):
                    st.session_state.selected_common_link_id = None
                st.session_state[key] = False

        for link_id in link_ids:
            st.session_state[f"common_link_select_{link_id}"] = (current_selected == link_id)

        for _, row in links_df.iterrows():
            link_id = int(row["id"])
            row_cols = st.columns([0.7, 2.2, 6.0, 1.5])
            row_cols[0].checkbox(
                "",
                key=f"common_link_select_{link_id}",
                on_change=handle_common_link_select,
                args=(link_id, link_ids),
                label_visibility="collapsed",
            )

            seq = format_common_link_seq(row.get("created_at", ""), link_id)
            url = str(row.get("url") or "")
            desc = str(row.get("description") or "")
            safe_seq = html.escape(seq)
            safe_url_text = html.escape(url)
            safe_url_href = html.escape(url, quote=True)
            safe_desc = html.escape(desc)

            row_cols[1].markdown(f'<div class="trade-row">{safe_desc}</div>', unsafe_allow_html=True)
            row_cols[2].markdown(
                f'<div class="trade-row"><a href="{safe_url_href}" target="_blank">{safe_url_text}</a></div>',
                unsafe_allow_html=True,
            )
            row_cols[3].markdown(f'<div class="trade-row">{safe_seq}</div>', unsafe_allow_html=True)


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

    c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 2, 1.5, 1.5, 1.5, 1.5, 1])
    # 初始化不设置日期过滤：默认显示所有 OPEN 记录；需要时再手动选择开始/结束日期。
    start_date = c1.date_input("开始日期", value=None, key="log_start_date")
    end_date = c2.date_input("结束日期", value=None, key="log_end_date")
    ticker_filter = c3.text_input("Ticker筛选", key="ticker_filter")
    status_options = ["ALL"] + RESULT_STATUSES
    default_status_index = status_options.index("OPEN") if "OPEN" in status_options else 0
    status_filter = c4.selectbox("状态", status_options, index=default_status_index, key="status_filter")
    strategy_filter = c5.selectbox("策略", ["ALL"] + list(STRATEGY_LABELS.keys()), 
                                   format_func=lambda x: x if x == "ALL" else format_strategy(x), key="strategy_filter")
    sub_type_filter = c6.selectbox("子类型", ["ALL"] + list(SUB_TYPE_LABELS.keys()), 
                                   format_func=lambda x: x if x == "ALL" else format_sub_type(x), key="sub_type_filter")
    # 用自定义 label，避免 st.markdown 默认段落边距把按钮挤得偏下
    c7.markdown(
        "<div style='height:26px; line-height:26px; font-weight:400; font-size:14px;'>操作</div>",
        unsafe_allow_html=True,
    )

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
    ensure_trade_indexes()
    df = get_trades(where_sql, params)
    if not df.empty and "is_deleted" in df.columns:
        df = df[pd.to_numeric(df["is_deleted"], errors="coerce").fillna(0).astype(int) == 0]
    df = add_related_info_to_df(df)
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
        "expiry_date",        # 到期日
        "result_status",      # 状态        
        "net_cash_flow",      # 净现金流
        "sub_type_display",   # 子类型
        "premium",            # 权利金
        "strike_price",       # 执行价1
        "strike_price_2",     # 执行价2
        "strike_price_3",     # 执行价3
        "strike_price_4",     # 执行价4
        "qty",                # 数量
        "closed_pnl",         # 已实现盈亏
        "trade_date",         # 交易日期
        "closed_date",        # 平仓日期
    ]

    table_cols = [c for c in table_cols if c in page_df.columns]

    header_widths = [
    0.6,   # 选择
    1.2,   # 编号
    1.2,   # 标的
    1.5,   # 策略
    1.1,   # 到期日
    1.0,   # 状态
    1.2,   # 净现金流
    1.5,   # 子类型
    1.0,   # 权利金
    1.0,   # 执行价1
    1.0,   # 执行价2
    1.0,   # 执行价3
    1.0,   # 执行价4
    0.8,   # 数量
    1.1,   # 已实现盈亏
    1.2,   # 交易日期
    1.2,   # 平仓日期
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

            # 到期日高亮规则：仅 OPEN 合约适用
            # 1) 非 OPEN 合约：不高亮，保持默认黑色字体
            # 2) 当前日期 > 到期日：不高亮，保持默认黑色字体
            # 3) 到期日距离今天 0~3 天：红色加粗
            # 4) 当前时间已经超过“交易日 -> 到期日”周期的一半：蓝色加粗
            #    例如 DTE=10，交易后过了 5 天，则到期日蓝色加粗
            if col == "expiry_date" and str(value).strip():
                try:
                    row_status = str(row.get("result_status", "")).upper()

                    if row_status == "OPEN":
                        exp_date = pd.to_datetime(value).date()
                        today_date = date.today()
                        days_left = (exp_date - today_date).days

                        if days_left < 0:
                            # 已经过期，不再高亮，保持默认黑色字体
                            style_extra = ""
                        elif 0 <= days_left <= 3:
                            style_extra = "color:#dc2626;font-weight:700;"
                        else:
                            trade_date_value = row.get("trade_date", "") or row.get("opened_date", "")
                            trade_dt = pd.to_datetime(trade_date_value, errors="coerce")

                            if pd.notna(trade_dt):
                                trade_dt = trade_dt.date()
                                total_days = (exp_date - trade_dt).days
                                elapsed_days = (today_date - trade_dt).days

                                if total_days > 0 and 0 <= elapsed_days <= total_days and elapsed_days >= total_days / 2:
                                    style_extra = "color:#2563eb;font-weight:700;"
                except Exception:
                    pass

            if col == "net_cash_flow" and value != "":
                 try:
                     num_value = float(value)
                     display_value = f"{num_value:.2f}"

                     if num_value < 0:
                         style_extra += "color:#16a34a;"
                 except Exception:
                     display_value = str(value)
            else:
                display_value = str(value)
            
            
            # 金额类字段统一显示 2 位小数
            money_cols = {"net_cash_flow", "premium", "fees", "closed_pnl","strike_price","strike_price_2","strike_price_3","strike_price_4"}

            if col in money_cols and value != "":
                try:
                    display_value = f"{float(value):.2f}"
                except Exception:
                    display_value =str(value)
            else:
                display_value = str(value)

            # 日志列表中，在执行价列前增加腿型前缀，例如 +P 100.00 / -C 160.00
            strike_symbol_map = {
                "strike_price": 0,
                "strike_price_2": 1,
                "strike_price_3": 2,
                "strike_price_4": 3,
            }
            if col in strike_symbol_map and str(display_value).strip():
                leg_symbols = get_strike_leg_symbols(row)
                leg_index = strike_symbol_map[col]
                if leg_index < len(leg_symbols) and str(leg_symbols[leg_index]).strip():
                    display_value = f"{leg_symbols[leg_index]} {display_value}"

            # 已关联合约的记录，在“标的”列增加统一“链接”图标；不同关联对使用不同颜色
            display_html = html.escape(str(display_value))
            if col == "ticker":
                try:
                    related_flag = int(float(row.get("has_related", 0) or 0)) == 1
                except Exception:
                    related_flag = False

                related_id = row.get("related_id", None)
                if related_flag and not pd.isna(related_id):
                    related_icon_html = get_related_trade_icon_html(rid, related_id)
                    if related_icon_html:
                        display_html = f'{html.escape(str(display_value))} {related_icon_html}'

            # 普通文本显示，不再给 position_id 增加链接
            row_cols[i].markdown(
                f'<div class="{row_class}" style="{style_extra}">{display_html}</div>',
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
                # 未平仓：显示修改、编辑、平仓、关联合约按钮
                btn_cols = st.columns([2, 2, 2, 2, 4])
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

                if btn_cols[3].button("🔗 关联合约", key=f"manage_related_open_{trade_id}"):
                    st.session_state.show_related_contract_dialog_trade_id = trade_id
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

                if btn_cols[2].button("🔗 关联合约", key=f"manage_related_closed_{trade_id}"):
                    st.session_state.show_related_contract_dialog_trade_id = trade_id
                    st.rerun()


            dialog_trade_id = st.session_state.get("show_review_dialog_trade_id")
            if dialog_trade_id == trade_id and result_status == "CLOSED":
                edit_review_dialog(trade_id, current_note)

            if st.session_state.get("show_related_contract_dialog_trade_id") == trade_id:
                related_contract_dialog(trade_id)


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
            with st.form(f"edit_trade_form_{int(target_trade_id)}"):
                top = st.columns(5)
                trade_date = top[0].date_input(req("交易日期"), value=as_date(target_trade.get("trade_date"), date.today()))
                opened_date = top[1].date_input(req("开仓日期"), value=as_date(target_trade.get("opened_date"), date.today()))
                strategy_options = list(STRATEGY_LABELS.keys())
                strategy_index = strategy_options.index(current_strategy) if current_strategy in strategy_options else 0

                # 先选策略，再根据策略限制动作和子类型；顶部顺序：期权策略 -> 动作 -> 子类型。
                option_strategy = top[2].selectbox(req("期权策略"), options=strategy_options, index=strategy_index, format_func=format_strategy)

                if option_strategy == "STOCK":
                    sub_type = None
                    action_options = get_action_options_for_strategy(option_strategy, sub_type)
                    action_value = str(target_trade.get("action") or action_options[0])
                    action_index = action_options.index(action_value) if action_value in action_options else 0
                    action = top[3].selectbox(req("动作"), options=action_options, index=action_index, format_func=format_action)
                    top[4].text_input("子类型", value="N/A", disabled=True)
                else:
                    sub_type_options = get_sub_type_options_for_strategy(option_strategy)
                    default_sub_type = str(current_sub_type or DEFAULT_SUB_TYPE_BY_STRATEGY.get(option_strategy) or sub_type_options[0])
                    sub_type_index = sub_type_options.index(default_sub_type) if default_sub_type in sub_type_options else 0
                    sub_type = top[4].selectbox(req("子类型"), options=sub_type_options, index=sub_type_index, format_func=format_sub_type)

                    action_options = get_action_options_for_strategy(option_strategy, sub_type)
                    action_value = str(target_trade.get("action") or action_options[0])
                    action_index = action_options.index(action_value) if action_value in action_options else 0
                    action = top[3].selectbox(req("动作"), options=action_options, index=action_index, format_func=format_action)

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

                # 修改页同样由期权策略自动确定方向；如果切换策略，会自动改为对应方向。
                option_right = get_default_option_right(option_strategy)
                r6[1].text_input("方向", value=option_right, disabled=True)

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
        option_strategy = str(target_trade.get("option_strategy") or "").upper()
        is_combo_strategy = option_strategy in COMBO_STRATEGIES
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
        current_result_status = str(target_trade.get("result_status") or "CLOSED")
        # 日志回填页面默认按“平仓”处理：OPEN 记录进入回填时，结果状态下拉框默认选 CLOSED
        if current_result_status == "OPEN":
            current_result_status = "CLOSED"
        if current_result_status not in RESULT_STATUSES:
            current_result_status = "CLOSED"
        result_status = c2.selectbox(req("结果状态"), RESULT_STATUSES, index=RESULT_STATUSES.index(current_result_status), key=result_key)
        assigned = c3.checkbox("是否被指派", value=bool(target_trade.get("assigned") or 0), key=assigned_key)
        execution_score = c4.selectbox(req("是否按计划执行"), EXECUTION_SCORES, index=EXECUTION_SCORES.index(str(target_trade.get("execution_score") or "YES")), key=exec_key)

        c5, c6, c7 = st.columns(3)
        if is_combo_strategy:
            close_price = c5.number_input(
                "平仓成交价格 / 组合净价",
                min_value=0.0,
                step=0.01,
                key=close_key,
                help="组合策略不使用单腿公式自动计算盈亏；这里可记录组合平仓净价，已实现盈亏请手动填写。",
            )
        else:
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

        if is_combo_strategy:
            st.info("组合策略（Collar / 合成空头 / 合成多头）：不使用单腿权利金公式自动计算，请直接填写组合已实现盈亏。")
        elif action == "BTO":
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
