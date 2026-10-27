"""
FX 资金看板 · Streamlit 版
===========================
数据源：Google Sheet「FX｜账户汇总 / FX｜出金记录 / 外汇汇总表」（只读）
运行：  streamlit run app.py
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
SHEET_ID = "1WluUTyvu0y7_xsAgR8qWatO6VukFsSwjBYGOtknqguU"
SOURCES = {
    "acct": ("1409811168", "FX｜账户汇总"),
    "wd":   ("707129201",  "FX｜出金记录"),
    "sum":  ("211498751",  "外汇汇总表"),
}
CACHE_TTL = 180  # 秒

# 主题色
C_POS, C_NEG = "#0a8754", "#c0392b"
C_BLUE, C_AMBER, C_VIOLET, C_SLATE = "#1f6feb", "#d97706", "#6d28d9", "#334155"

STATUS_COLOR = {
    "使用中": C_BLUE, "出金中": C_AMBER, "已出金": C_POS, "扣盈利": C_NEG,
    "坏账": "#991b1b", "部分出金": C_VIOLET, "爆仓亏损": "#7f1d1d", "—": "#98a2b3",
}

st.set_page_config(page_title="FX 资金看板", page_icon="📊", layout="wide")


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def norm_num(x) -> float:
    """把 '  $19,856.00 ' / '-$27,455.59' / '-' 解析成 float。"""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s or s in ("-", "—"):
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("%", "").replace(" ", "")
    if s.startswith("-"):
        neg, s = True, s[1:]
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def parse_date(s):
    """表格里是 dd/mm/yyyy。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s == "-":
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def usd(v: float) -> str:
    sign = "-" if v < -0.005 else ""
    return f"{sign}${abs(v):,.2f}"


def pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def dur_txt(hours: float | None) -> str:
    if hours is None:
        return "—"
    return f"{hours:.1f} 小时" if hours < 48 else f"{hours / 24:.1f} 天"


# ----------------------------------------------------------------------------
# 数据获取
# ----------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_csv(gid: str) -> str:
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/export?format=csv&gid={gid}&_={int(datetime.now().timestamp())}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read().decode("utf-8-sig")
    if raw.lstrip()[:200].lower().startswith("<!doctype html") or "<html" in raw[:400].lower():
        raise RuntimeError("返回了登录页而不是 CSV，请检查表格的共享权限")
    return raw


def to_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def header_index(rows, anchor: str, limit: int = 30):
    for i, r in enumerate(rows[:limit]):
        if r and r[0].strip() == anchor:
            return i
    return -1


def header_map(row) -> dict[str, int]:
    ix = {}
    for i, h in enumerate(row):
        h = (h or "").strip()
        if h and h not in ix:
            ix[h] = i
    return ix


def cell(row, ix, key, default=""):
    i = ix.get(key)
    if i is None or i >= len(row):
        return default
    return row[i]


# ----------------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------------
def parse_accounts(rows) -> pd.DataFrame:
    hi = header_index(rows, "序号")
    if hi < 0:
        raise RuntimeError("账户汇总：找不到表头「序号」")
    ix = header_map(rows[hi])

    need = ["批次", "归属", "用户名", "平台", "入金日期", "入金量", "到账日期",
            "出金实际到款", "出金手续费", "入金手续费", "账户实际盈利",
            "未到账金额", "扣盈利", "账户余额", "账户状态"]
    miss = [k for k in need if k not in ix]
    if miss:
        raise RuntimeError(f"账户汇总：缺列 {miss}")

    out = []
    for r in rows[hi + 1:]:
        if not r:
            continue
        user = cell(r, ix, "用户名").strip()
        plat = cell(r, ix, "平台").strip()
        if not user or user == "-" or plat == "-":
            continue  # 占位空行
        batch = cell(r, ix, "批次").strip() or "养户（未分批）"
        out.append({
            "批次": batch,
            "归属": cell(r, ix, "归属").strip(),
            "用户名": user,
            "平台": plat,
            "入金日期": parse_date(cell(r, ix, "入金日期")),
            "入金": norm_num(cell(r, ix, "入金量")),
            "到账日期": parse_date(cell(r, ix, "到账日期")),
            "出金到账": norm_num(cell(r, ix, "出金实际到款")),
            "出金手续费": norm_num(cell(r, ix, "出金手续费")),
            "入金手续费": norm_num(cell(r, ix, "入金手续费")),
            "账面盈利": norm_num(cell(r, ix, "账户实际盈利")),
            "在出金中": norm_num(cell(r, ix, "未到账金额")),
            "扣盈利": norm_num(cell(r, ix, "扣盈利")),
            "余额": norm_num(cell(r, ix, "账户余额")),
            "状态": cell(r, ix, "账户状态").strip() or "—",
        })
    return pd.DataFrame(out)


def parse_withdrawals(rows) -> pd.DataFrame:
    hi = header_index(rows, "批次")
    if hi < 0:
        raise RuntimeError("出金记录：找不到表头「批次」")
    ix = header_map(rows[hi])

    def ts(date_s, time_s):
        d = parse_date(date_s)
        if not d:
            return None
        m = re.match(r"^(\d{1,2}):(\d{2})", (time_s or "").strip())
        if m:
            d = d.replace(hour=int(m.group(1)), minute=int(m.group(2)))
        return d

    out = []
    for r in rows[hi + 1:]:
        if not r:
            continue
        batch = cell(r, ix, "批次").strip()
        if not batch:
            continue
        wd_date, wd_time = cell(r, ix, "出金日期"), cell(r, ix, "出金时间")
        out.append({
            "批次": batch,
            "归属": cell(r, ix, "归属").strip(),
            "编号": cell(r, ix, "编号").strip(),
            "交易账户": cell(r, ix, "交易账户").strip(),
            "平台": cell(r, ix, "平台").strip(),
            "本金": norm_num(cell(r, ix, "本金")),
            "申请出金金额": norm_num(cell(r, ix, "申请出金金额")),
            "正在出金金额": norm_num(cell(r, ix, "正在出金金额")),
            "状态": cell(r, ix, "账户状态").strip(),
            "出金日期": parse_date(wd_date),
            "出金时间": (wd_time or "").strip(),
            "出金时间戳": ts(wd_date, wd_time),
            "到账日期": parse_date(cell(r, ix, "到账日期")),
            "出金实际到账": norm_num(cell(r, ix, "出金实际到账")),
            "手续费": norm_num(cell(r, ix, "手续费")),
            "账户盈利": norm_num(cell(r, ix, "账户盈利")),
            "未到账金额": norm_num(cell(r, ix, "未到账金额")),
            "扣盈利金额": norm_num(cell(r, ix, "扣盈利金额")),
        })
    return pd.DataFrame(out)


def parse_summary(rows) -> dict:
    cut = None
    for r in rows[:6]:
        for c, v in enumerate(r):
            if str(v).strip() == "数据截止至":
                cut = parse_date(r[c + 1]) if c + 1 < len(r) else None
    total = {}
    for r in rows:
        if len(r) > 8 and r[1].strip() == "总计":
            total = {
                "总投入": norm_num(r[3]), "账户余额": norm_num(r[4]),
                "出金到账": norm_num(r[5]), "手续费": norm_num(r[6]),
                "账户盈利": norm_num(r[7]), "坏账": norm_num(r[8]),
            }
            break
    return {"cut": cut, "total": total}


# ----------------------------------------------------------------------------
# 指标
# ----------------------------------------------------------------------------
def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(n=0, invest=0, cashIn=0, profit=0, pending=0, withheld=0, balance=0,
                    depFee=0, wdFee=0, pendingProfit=0, realized=0, pendingPrincipal=0,
                    equity=0, rBook=0, rReal=0, rCash=0, rPending=0, rWithheld=0)
    invest = df["入金"].sum()
    cashIn = df["出金到账"].sum()
    profit = df["账面盈利"].sum()
    pending = df["在出金中"].sum()
    withheld = df["扣盈利"].sum()
    balance = df["余额"].sum()
    in_flight = df["状态"].isin(["出金中", "部分出金"])
    pending_profit = df.loc[in_flight, "账面盈利"].sum()
    realized = profit - pending_profit
    return dict(
        n=len(df), invest=invest, cashIn=cashIn, profit=profit, pending=pending,
        withheld=withheld, balance=balance,
        depFee=df["入金手续费"].sum(), wdFee=df["出金手续费"].sum(),
        pendingProfit=pending_profit, realized=realized,
        pendingPrincipal=pending - pending_profit,
        equity=cashIn + balance,
        rBook=profit / invest if invest else 0,
        rReal=realized / invest if invest else 0,
        rCash=cashIn / invest if invest else 0,
        rPending=pending / invest if invest else 0,
        rWithheld=withheld / (profit + withheld) if (profit + withheld) else 0,
    )


# ----------------------------------------------------------------------------
# 侧边栏
# ----------------------------------------------------------------------------
st.sidebar.title("📊 FX 资金看板")
st.sidebar.caption("Forex-2026 · 只读")

auto = st.sidebar.toggle("自动刷新（3 分钟）", value=False)
if st.sidebar.button("🔄 刷新数据", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()

# ----------------------------------------------------------------------------
# 加载
# ----------------------------------------------------------------------------
data = {}
errors = {}
for key, (gid, label) in SOURCES.items():
    try:
        data[key] = to_rows(fetch_csv(gid))
    except Exception as e:  # noqa: BLE001
        errors[key] = f"{label}：{e}"

if errors:
    st.error("部分数据源加载失败\n\n" + "\n".join(f"- {v}" for v in errors.values())
             + "\n\n请确认表格仍为「知道链接的任何人 · 查看者」。")
    st.stop()

try:
    accounts = parse_accounts(data["acct"])
    withdrawals = parse_withdrawals(data["wd"])
    summary = parse_summary(data["sum"])
except Exception as e:  # noqa: BLE001
    st.error(f"解析失败：{e}")
    st.stop()

# ----------------------------------------------------------------------------
# 筛选
# ----------------------------------------------------------------------------
BATCH_ORDER = {"第一批": 0, "第二批": 1, "第三批": 2}
batches = sorted(accounts["批次"].unique(),
                 key=lambda b: (BATCH_ORDER.get(b, 3), b))
counts = accounts["批次"].value_counts().to_dict()

sel_batch = st.sidebar.radio(
    "批次",
    ["全部"] + batches,
    format_func=lambda b: "全部" if b == "全部" else f"{b}（{counts.get(b, 0)}）",
)
statuses = ["全部"] + sorted(accounts["状态"].unique())
sel_status = st.sidebar.selectbox("账户状态", statuses)

st.sidebar.divider()
if summary.get("cut"):
    st.sidebar.caption(f"数据截止：{summary['cut']:%Y-%m-%d}")
st.sidebar.caption("数据源：FX｜账户汇总 · 出金记录 · 外汇汇总表")

acc = accounts if sel_batch == "全部" else accounts[accounts["批次"] == sel_batch]
if sel_status != "全部":
    acc = acc[acc["状态"] == sel_status]
wd = withdrawals if sel_batch == "全部" else withdrawals[withdrawals["批次"] == sel_batch]

m = metrics(acc)

# ----------------------------------------------------------------------------
# 头部
# ----------------------------------------------------------------------------
cut_txt = f"{summary['cut']:%Y-%m-%d}" if summary.get("cut") else "—"
scope = " · ".join(x for x in [sel_batch if sel_batch != "全部" else None,
                               sel_status if sel_status != "全部" else None] if x)

c_title, c_meta = st.columns([3, 1])
with c_title:
    st.title("FX 资金看板")
    st.caption(f"数据截止 {cut_txt}" + (f" · 当前范围：{scope}" if scope else f" · 全部 {len(acc)} 户"))
with c_meta:
    st.caption(f"更新于 {datetime.now():%H:%M:%S}")


# ----------------------------------------------------------------------------
# 组件
# ----------------------------------------------------------------------------
def kpi(col, label, value, tag=None, tag_color=C_BLUE, foot=""):
    with col:
        st.metric(label, value, help=foot)
        if tag:
            st.markdown(
                f"<span style='background:{tag_color}1a;color:{tag_color};padding:2px 8px;"
                f"border-radius:6px;font-size:11.5px;font-weight:600'>{tag}</span>",
                unsafe_allow_html=True,
            )


def bar_row(label, value, max_value, color, right_text, label_w=None):
    pct_w = 0 if not max_value else max(0, min(100, value / max_value * 100))
    st.markdown(
        f"""
        <div style="display:grid;grid-template-columns:120px 1fr 170px;gap:10px;
                    align-items:center;padding:4px 0">
          <div style="font-size:12.5px;color:#3d4657;font-weight:550">{label}</div>
          <div style="height:8px;background:#eef0f4;border-radius:5px;overflow:hidden">
            <div style="width:{pct_w:.1f}%;height:100%;background:{color};border-radius:5px"></div>
          </div>
          <div style="text-align:right;font-size:12.5px;font-weight:650;
                      font-variant-numeric:tabular-nums">{right_text}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def kv_row(label, value, color="#0d1117"):
    st.markdown(
        f"""<div style="display:flex;justify-content:space-between;gap:10px;
                padding:6px 0;border-bottom:1px dashed #eef0f4;font-size:12.5px">
              <span style="color:#667085">{label}</span>
              <b style="color:{color};font-variant-numeric:tabular-nums">{value}</b>
            </div>""",
        unsafe_allow_html=True,
    )


def _sign_color(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"color: {C_POS}" if v > 0.005 else (f"color: {C_NEG}" if v < -0.005 else "")


def _pct_color(txt):
    try:
        v = float(str(txt).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return ""
    return f"color: {C_POS}" if v > 0.005 else (f"color: {C_NEG}" if v < -0.005 else "")


# ----------------------------------------------------------------------------
# KPI
# ----------------------------------------------------------------------------
st.subheader("核心指标")
r1 = st.columns(3)
kpi(r1[0], "总投入本金", usd(m["invest"]), f"{m['n']} 个账户", C_SLATE,
    f"入金手续费 {usd(m['depFee'])}")
kpi(r1[1], "出金已到账", usd(m["cashIn"]), f"回收 {pct(m['rCash'])}", C_BLUE,
    f"出金手续费 {usd(m['wdFee'])}")
kpi(r1[2], "账面盈利", usd(m["profit"]), f"账面收益率 {pct(m['rBook'])}", C_POS,
    f"账面权益 {usd(m['equity'])}")

r2 = st.columns(3)
kpi(r2[0], "利润已到账", usd(m["realized"]), f"到手收益率 {pct(m['rReal'])}", C_POS,
    f"占账面盈利 {pct(m['realized'] / m['profit']) if m['profit'] else '—'}")
kpi(r2[1], "本金+利润在出金中", usd(m["pending"]), f"在途率 {pct(m['rPending'])}", C_AMBER,
    f"本金 {usd(m['pendingPrincipal'])} · 利润 {usd(m['pendingProfit'])}")
kpi(r2[2], "利润被扣盈利", usd(m["withheld"]), "坏账口径", C_NEG,
    f"占（盈利+扣盈利）{pct(m['rWithheld'])}，不计入账面盈利")

st.divider()

# ----------------------------------------------------------------------------
# 资金全景 + 收益率
# ----------------------------------------------------------------------------
st.subheader("资金全景与收益率")
col_wf, col_rate = st.columns([1.35, 1], gap="medium")

with col_wf:
    st.markdown("**资金去向**")
    equity = m["equity"] or 1
    segs = [
        ("已到账", m["cashIn"], C_POS),
        ("在出金中", m["pending"], C_AMBER),
        ("账户内（使用中）", max(0, m["balance"] - m["pending"]), C_BLUE),
    ]
    total_known = sum(s[1] for s in segs) or 1
    html = "".join(
        f"<div style='width:{v / total_known * 100:.2f}%;background:{c};height:32px'></div>"
        for _, v, c in segs
    )
    st.markdown(
        f"<div style='display:flex;border-radius:9px;overflow:hidden'>{html}</div>",
        unsafe_allow_html=True,
    )
    legend = "".join(
        f"<span style='margin-right:16px;font-size:12px;color:#667085'>"
        f"<i style='display:inline-block;width:9px;height:9px;border-radius:3px;"
        f"background:{c};margin-right:6px'></i>{k} <b style='color:#3d4657'>{usd(v)}</b></span>"
        for k, v, c in segs
    )
    st.markdown(f"<div style='margin:10px 0 14px'>{legend}</div>", unsafe_allow_html=True)

    kv_row("总投入", usd(m["invest"]))
    kv_row("+ 账面盈利", usd(m["profit"]))
    kv_row("= 账面权益", usd(m["equity"]))

with col_rate:
    st.markdown("**收益率对照**")
    bar_row("账面收益率", m["rBook"], 0.20, C_POS, pct(m["rBook"]))
    bar_row("到手收益率", m["rReal"], 0.20, C_BLUE, pct(m["rReal"]))
    bar_row("现金回收率", m["rCash"], 1.00, C_VIOLET, pct(m["rCash"]))
    bar_row("在途占比", m["rPending"], 0.20, C_AMBER, pct(m["rPending"]))
    st.caption("进度条满刻度：前三项 20% / 100% / 100%，在途占比 20%")

st.divider()

# ----------------------------------------------------------------------------
# 批次对比
# ----------------------------------------------------------------------------
st.subheader("第一批 vs 第二批")
rows = []
for b in batches:
    sub = accounts[accounts["批次"] == b]
    mm = metrics(sub)
    rows.append({
        "批次": b,
        "账户数": mm["n"],
        "总投入": mm["invest"],
        "出金到账": mm["cashIn"],
        "账面盈利": mm["profit"],
        "在出金中": mm["pending"],
        "扣盈利": mm["withheld"],
        "账面收益率": mm["rBook"],
        "到手收益率": mm["rReal"],
        "已回款户数": int((sub["出金到账"] > 0).sum()),
        "在途户数": int((sub["在出金中"] > 0).sum()),
    })
if rows:
    bt = pd.DataFrame(rows).set_index("批次")
    st.dataframe(
        bt.style.format({
            "总投入": usd, "出金到账": usd, "账面盈利": usd, "在出金中": usd, "扣盈利": usd,
            "账面收益率": pct, "到手收益率": pct,
        }).map(
            _sign_color, subset=["账面盈利"]
        ).map(
            _pct_color, subset=["账面收益率", "到手收益率"]
        ),
        use_container_width=True,
    )

st.divider()

# ----------------------------------------------------------------------------
# 出金中 & 坏账
# ----------------------------------------------------------------------------
st.subheader("出金中 & 坏账")
col_p, col_b = st.columns([1.62, 1], gap="medium")

with col_p:
    st.markdown("**出金中在途**")
    pend = wd[wd["状态"] == "出金中"].copy()
    if pend.empty:
        st.info("当前范围内没有「出金中」的账户")
    else:
        now = datetime.now()
        pend["已持续"] = pend["出金时间戳"].apply(
            lambda t: (now - t).total_seconds() / 3600 if pd.notna(t) else None
        )
        total, profit = pend["未到账金额"].sum(), pend["账户盈利"].sum()
        principal = total - profit
        submitted = pend["正在出金金额"].sum()
        unsubmitted = total - submitted

        a, b_, c = st.columns(3)
        a.metric("在途合计", usd(total), help=f"已提交出金 {usd(submitted)}")
        b_.metric("在途本金", usd(principal), help=f"占在途 {pct(principal / total) if total else '—'}")
        c.metric("在途盈利", usd(profit), help=f"占在途 {pct(profit / total) if total else '—'}")

        buckets = [("≤ 36 小时", 0, 36, C_POS), ("36 小时 – 3 天", 36, 72, C_BLUE),
                   ("3 – 7 天", 72, 168, C_AMBER), ("超过 7 天", 168, float("inf"), C_NEG)]
        st.markdown("**出金已持续时长分布**")
        vals = []
        for label, lo, hi, color in buckets:
            sub = pend[pend["已持续"].apply(lambda h: h is not None and lo < h <= hi)]
            vals.append((label, len(sub), sub["未到账金额"].sum(), color))
        max_amt = max([v[2] for v in vals] + [1])
        for label, cnt, amt, color in vals:
            bar_row(label, amt, max_amt, color, f"{cnt} 笔 · {usd(amt)}")

        hs = sorted([h for h in pend["已持续"].dropna()])
        if hs:
            med = hs[len(hs) // 2] if len(hs) % 2 else (hs[len(hs) // 2 - 1] + hs[len(hs) // 2]) / 2
            st.caption(f"最短 {dur_txt(hs[0])} · 中位 {dur_txt(med)} · 最长 {dur_txt(hs[-1])}"
                       + (f"　|　其中 {usd(unsubmitted)} 仍留在账户内、尚未提交出金"
                          if unsubmitted > 0.01 else ""))

        if vals[0][1] + vals[1][1] == 0:
            st.warning(f"{len(pend)} 笔出金**全部超过 7 天**仍未到账，合计 {usd(total)}"
                       f"（本金 {usd(principal)}、盈利 {usd(profit)}）。正常 36 小时内应到账。")

        show = pend.sort_values("已持续", ascending=False)[
            ["批次", "交易账户", "平台", "出金日期", "出金时间", "已持续",
             "本金", "账户盈利", "未到账金额"]
        ].rename(columns={"账户盈利": "盈利", "未到账金额": "在途"})
        show["出金日期"] = show["出金日期"].dt.strftime("%Y-%m-%d")
        show["已持续"] = show["已持续"].apply(dur_txt)
        st.dataframe(
            show.style.format({"本金": usd, "盈利": usd, "在途": usd}),
            use_container_width=True, height=260, hide_index=True,
        )

with col_b:
    st.markdown("**坏账 / 扣盈利**")
    bad = wd[wd["扣盈利金额"] > 0.005].copy()
    if bad.empty:
        st.info("当前范围内没有坏账 / 扣盈利记录")
    else:
        withheld = bad["扣盈利金额"].sum()
        principal = bad.apply(lambda r: max(0, r["本金"] - r["出金实际到账"]), axis=1).sum()
        tot = withheld + principal
        recovered = bad["出金实际到账"].sum()
        prin_inv = bad["本金"].sum()

        st.markdown(
            f"<div style='font-size:27px;font-weight:690;color:{C_NEG};letter-spacing:-.6px'>"
            f"{usd(withheld)}</div>"
            f"<div style='font-size:11.5px;color:#667085;margin-bottom:12px'>"
            f"被扣盈利 · 坏账合计 {usd(tot)} · {len(bad)} 个账户</div>",
            unsafe_allow_html=True,
        )
        w_pct = withheld / tot * 100 if tot else 0
        p_pct = principal / tot * 100 if tot else 0
        st.markdown(
            f"""<div style="display:flex;height:24px;border-radius:9px;overflow:hidden">
                  <div style="width:{w_pct:.1f}%;background:{C_NEG};color:#fff;font-size:11px;
                              display:flex;align-items:center;justify-content:center">
                    {f'盈利 {w_pct:.0f}%' if w_pct > 12 else ''}</div>
                  <div style="width:{p_pct:.1f}%;background:#7f1d1d;color:#fff;font-size:11px;
                              display:flex;align-items:center;justify-content:center">
                    {f'本金 {p_pct:.0f}%' if p_pct > 12 else ''}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='margin:8px 0 12px;font-size:12px;color:#667085'>"
            f"<i style='display:inline-block;width:9px;height:9px;border-radius:3px;"
            f"background:{C_NEG};margin-right:6px'></i>被扣盈利 {usd(withheld)}"
            f"<span style='margin-left:16px'><i style='display:inline-block;width:9px;height:9px;"
            f"border-radius:3px;background:#7f1d1d;margin-right:6px'></i>"
            f"被扣本金 {usd(principal)}</span></div>",
            unsafe_allow_html=True,
        )
        kv_row("被扣盈利", usd(withheld), C_NEG)
        kv_row("被扣本金", usd(principal))
        kv_row("本金收回情况", f"{usd(recovered)} / {usd(prin_inv)}")
        kv_row("占账面盈利", pct(withheld / m["profit"]) if m["profit"] else "—")
        kv_row("占全部投入", pct(tot / m["invest"]) if m["invest"] else "—")

        for _, r in bad.iterrows():
            st.caption(
                f"**{r['编号']}** · {r['平台']} · {r['批次']}　"
                f"投入 {usd(r['本金'])}，本金到账 {usd(r['出金实际到账'])}；"
                f"账面盈利 {usd(r['账户盈利'])}，另有 {usd(r['扣盈利金额'])} 被平台扣除"
            )
        st.caption(f"本金{'有' if principal > 0.01 else '无'}损失：坏账 {pct(principal / tot) if tot else '0%'} "
                   f"落在本金、{pct(withheld / tot) if tot else '0%'} 落在利润。")

st.divider()

# ----------------------------------------------------------------------------
# 归属 / 状态
# ----------------------------------------------------------------------------
col_o, col_s = st.columns(2, gap="medium")

with col_o:
    st.markdown("**归属表现**")
    own = []
    for k, sub in acc.groupby("归属"):
        mm = metrics(sub)
        own.append({"归属": k or "—", "账户数": mm["n"], "入金": mm["invest"],
                    "出金到账": mm["cashIn"], "账面盈利": mm["profit"],
                    "账面收益率": mm["rBook"]})
    if own:
        st.dataframe(
            pd.DataFrame(own).sort_values("入金", ascending=False).set_index("归属")
              .style.format({"入金": usd, "出金到账": usd, "账面盈利": usd, "账面收益率": pct}),
            use_container_width=True,
        )

with col_s:
    st.markdown("**账户状态分布**")
    st_data = []
    for k, sub in acc.groupby("状态"):
        st_data.append((k, len(sub), sub["入金"].sum()))
    st_data.sort(key=lambda x: -x[2])
    mx = max([v[2] for v in st_data] + [1])
    for k, cnt, amt in st_data:
        bar_row(f"{k}", amt, mx, STATUS_COLOR.get(k, "#98a2b3"),
                f"{usd(amt)} / {cnt}户")

st.divider()

# ----------------------------------------------------------------------------
# 时间线
# ----------------------------------------------------------------------------
st.subheader("资金流时间线")
st.caption("按日聚合；入金按入金日期，到账按到账日期")

dep = acc.dropna(subset=["入金日期"]).groupby(acc["入金日期"].dt.date)["入金"].sum()
wdd = acc.dropna(subset=["到账日期"]).groupby(acc["到账日期"].dt.date)["出金到账"].sum()

if len(dep) or len(wdd):
    all_days = list(dep.index) + list(wdd.index)
    idx = pd.date_range(min(all_days), max(all_days), freq="D")
    xs = [d.date() for d in idx]
    ys_dep = [dep.get(d, 0) for d in xs]
    ys_wd = [wdd.get(d, 0) for d in xs]

    fig = go.Figure()
    fig.add_bar(x=xs, y=ys_dep, name="入金", marker_color=C_BLUE,
                hovertemplate="%{x}<br>入金 $%{y:,.2f}<extra></extra>")
    fig.add_bar(x=xs, y=ys_wd, name="出金到账", marker_color=C_POS,
                hovertemplate="%{x}<br>到账 $%{y:,.2f}<extra></extra>")
    fig.update_layout(
        barmode="group", bargap=0.25, bargroupgap=0.05,
        height=280, margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(tickformat="%m-%d", tickangle=0, showgrid=False),
        yaxis=dict(tickformat="$,.0f", gridcolor="#eef0f4", zeroline=False),
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="-apple-system, PingFang SC, sans-serif", size=12),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    cc1, cc2 = st.columns(2)
    cc1.caption(f"入金合计 {usd(sum(ys_dep))} · {len(dep)} 天有记录")
    cc2.caption(f"到账合计 {usd(sum(ys_wd))} · {len(wdd)} 天有记录")
else:
    st.info("当前范围内没有带日期的记录")

st.divider()

# ----------------------------------------------------------------------------
# 明细
# ----------------------------------------------------------------------------
st.subheader("明细")
tab_acc, tab_wd = st.tabs(["账户明细", "出金流水"])

with tab_acc:
    q = st.text_input("搜索 用户名 / 平台 / 归属", key="q_acc", placeholder="输入关键词过滤")
    view = acc
    if q:
        mask = (acc["用户名"] + " " + acc["平台"] + " " + acc["归属"]).str.contains(
            re.escape(q), case=False, na=False)
        view = acc[mask]
    show = view[["批次", "用户名", "平台", "入金", "出金到账", "账面盈利",
                 "在出金中", "扣盈利", "余额", "状态"]].copy()
    st.dataframe(
        show.style.format({"入金": usd, "出金到账": usd, "账面盈利": usd,
                           "在出金中": usd, "扣盈利": usd, "余额": usd}),
        use_container_width=True, height=420, hide_index=True,
    )
    vm = metrics(view)
    st.caption(f"共 {len(view)} 户　|　入金 {usd(vm['invest'])}　到账 {usd(vm['cashIn'])}　"
               f"账面盈利 {usd(vm['profit'])}　在出金中 {usd(vm['pending'])}")

with tab_wd:
    q2 = st.text_input("搜索 交易账户 / 平台 / 归属", key="q_wd", placeholder="输入关键词过滤")
    view2 = wd
    if q2:
        mask = (wd["交易账户"] + " " + wd["平台"] + " " + wd["归属"]).str.contains(
            re.escape(q2), case=False, na=False)
        view2 = wd[mask]
    show2 = view2[["批次", "交易账户", "平台", "出金日期", "申请出金金额",
                   "到账日期", "出金实际到账", "手续费", "账户盈利", "状态"]].copy()
    for c in ("出金日期", "到账日期"):
        show2[c] = show2[c].apply(lambda d: d.strftime("%Y-%m-%d") if pd.notna(d) else "—")
    st.dataframe(
        show2.style.format({"申请出金金额": usd, "出金实际到账": usd,
                            "手续费": usd, "账户盈利": usd}),
        use_container_width=True, height=420, hide_index=True,
    )
    st.caption(f"共 {len(view2)} 笔　|　申请 {usd(view2['申请出金金额'].sum())}　"
               f"到账 {usd(view2['出金实际到账'].sum())}　"
               f"手续费 {usd(view2['手续费'].sum())}")

# ----------------------------------------------------------------------------
# 对账
# ----------------------------------------------------------------------------
st.divider()
st.markdown("**对账检查**")
tot = summary.get("total", {})
checks = [
    ("总投入", m["invest"], tot.get("总投入", 0)),
    ("出金到账", m["cashIn"], tot.get("出金到账", 0)),
    ("账户盈利", m["profit"], tot.get("账户盈利", 0)),
    ("账户余额", m["balance"], tot.get("账户余额", 0)),
    ("扣盈利", m["withheld"], tot.get("坏账", 0)),
]
if sel_batch == "全部" and sel_status == "全部" and tot:
    diffs = [(k, a, b) for k, a, b in checks if abs(a - b) > 0.02]
    if diffs:
        st.warning("对账差异：" + "；".join(f"{k} 差 {usd(a - b)}" for k, a, b in diffs))
    else:
        st.success(
            f"对账通过：5 项核心口径与「外汇汇总表」总计行完全一致"
            f"（手续费合计 {usd(m['depFee'] + m['wdFee'])}，汇总表 {usd(tot.get('手续费', 0))}）"
        )
else:
    st.caption("对账仅在「全部批次 + 全部状态」下校验；当前有筛选条件，已跳过。")

with st.expander("口径说明"):
    st.markdown(
        """
- **总投入** = Σ 入金量
- **出金已到账** = Σ 出金实际到款
- **账面盈利** = Σ 账户实际盈利
- **利润已到账** = 账面盈利 − 在途账户的盈利
- **本金+利润在出金中** = Σ 未到账金额（已含在“账户余额”内，不重复相加）
- **利润被扣盈利** = Σ 扣盈利（坏账口径，不计入账面盈利）
- **账面权益** = 出金到账 + 账户余额
- **在途本金** = 未到账金额 − 在途盈利；**已持续时长** = 当前时刻 − 出金日期时间
- **被扣本金** = max(0, 本金 − 已到账)
- 「FX｜入金记录」含后台账号与密码字段，未接入；入金明细由「账户汇总」覆盖
        """
    )

if auto:
    import time
    time.sleep(CACHE_TTL)
    st.rerun()
