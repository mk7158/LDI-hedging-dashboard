"""
app.py
------
LDI Hedging Dashboard — Streamlit UI

Run with:
    streamlit run app.py

Requirements:
    pip install streamlit pandas numpy plotly
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from ldi_engine import (
    calculate_liabilities,
    get_assets,
    optimize_portfolio,
    get_pv01_buckets,
    get_yield_curve,
    get_liability_cashflows,
    sensitivity_table,
)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LDI Hedging Dashboard",
    page_icon="📊",
    layout="wide",
)

# ── Sidebar: scenario controls ────────────────────────────────────────────────
st.sidebar.header("Scenario Controls")
shock_bps = st.sidebar.slider(
    "Yield Curve Shock (bps)", min_value=-100, max_value=100, value=0, step=5
)
capital_m = st.sidebar.number_input(
    "Available Capital ($M)", min_value=50, max_value=2000, value=500, step=10
)
st.sidebar.markdown("---")
st.sidebar.markdown("**Asset Universe**")

# ── Compute ───────────────────────────────────────────────────────────────────
liab     = calculate_liabilities(shock_bps)
assets   = get_assets(shock_bps)
portfolio = optimize_portfolio(assets, liab["pv01"], capital_m)

# Show asset universe PV01s in sidebar
for _, row in assets.iterrows():
    label = f"{'🔵' if row['Type'] == 'Treasury' else '🟣'} {row['Instrument']}"
    st.sidebar.caption(f"{label} — PV01 ${row['PV01_per_1M']:,.0f}/M")

# ── Header ────────────────────────────────────────────────────────────────────
st.title("LDI Hedging Dashboard")
st.caption("Liability-Driven Investment · US Treasury & USD Swap Hedging")

# ── KPI row ───────────────────────────────────────────────────────────────────
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

kpi1.metric(
    "Liability NPV",
    f"${liab['npv'] / 1e6:,.1f}M",
    help="30Y USD pension liability ($15M × 1.025^t)",
)
kpi2.metric(
    "Liability PV01",
    f"${liab['pv01']:,.0f}",
    delta=f"After {shock_bps:+d} bps shock" if shock_bps != 0 else "Base scenario",
    delta_color="inverse" if shock_bps > 0 else "normal",
)

if portfolio is not None:
    total_pv01   = portfolio["Allocated_PV01"].sum()
    hedge_ratio  = total_pv01 / liab["pv01"] * 100
    total_deployed = portfolio["Optimal_Notional_M"].sum()
    duration_gap = (total_pv01 - liab["pv01"]) / liab["pv01"] * 100

    kpi3.metric(
        "Hedge Ratio",
        f"{hedge_ratio:.1f}%",
        delta=f"{hedge_ratio - 100:+.1f}% vs target",
        delta_color="normal" if abs(hedge_ratio - 100) <= 5 else "inverse",
    )
    kpi4.metric(
        "Capital Deployed",
        f"${total_deployed:,.1f}M",
        delta=f"{total_deployed / capital_m * 100:.0f}% of ${capital_m}M budget",
        delta_color="off",
    )
    kpi5.metric(
        "Duration Gap",
        f"{duration_gap:+.2f}%",
        delta="Within tolerance" if abs(duration_gap) <= 5 else "Outside tolerance",
        delta_color="normal" if abs(duration_gap) <= 5 else "inverse",
    )
else:
    kpi3.error("Optimizer infeasible")
    kpi4.metric("Capital Deployed", "—")
    kpi5.metric("Duration Gap", "—")

st.markdown("---")

# ── Main charts + trade table ─────────────────────────────────────────────────
if portfolio is not None:
    tab_pv01, tab_cashflows, tab_yield, tab_sensitivity = st.tabs(
        ["📊 KRD Match", "💰 Cashflows", "📈 Yield Curve", "🔢 Sensitivity"]
    )

    # ── Tab 1: KRD bucket match ───────────────────────────────────────────────
    with tab_pv01:
        buckets = get_pv01_buckets(liab["pv01"], portfolio)

        st.markdown(
            "Per-bucket KRD match — each bucket constrained to **±5%** of liability PV01"
        )

        # Status badges
        badge_cols = st.columns(len(buckets))
        for col, (_, row) in zip(badge_cols, buckets.iterrows()):
            icon  = "✅" if row["Within_Tol"] else "❌"
            color = "green" if row["Within_Tol"] else "red"
            col.markdown(
                f"<div style='text-align:center; color:{color}; font-weight:600'>"
                f"{icon} {row['Bucket'].split()[0]}</div>",
                unsafe_allow_html=True,
            )

        # Bar chart
        fig = go.Figure(data=[
            go.Bar(
                name="Liability PV01",
                x=buckets["Bucket"],
                y=buckets["Liability_PV01"],
                marker_color="#E74C3C",
            ),
            go.Bar(
                name="Hedged Asset PV01",
                x=buckets["Bucket"],
                y=buckets["Hedged_PV01"],
                marker_color="#2E86C1",
            ),
        ])
        fig.update_layout(
            barmode="group",
            template="plotly_white",
            height=380,
            margin=dict(t=20, b=20),
            yaxis_tickprefix="$",
            yaxis_tickformat=",.0f",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Deviation cards
        dev_cols = st.columns(len(buckets))
        for col, (_, row) in zip(dev_cols, buckets.iterrows()):
            bg    = "#d4edda" if row["Within_Tol"] else "#f8d7da"
            color = "#155724" if row["Within_Tol"] else "#721c24"
            col.markdown(
                f"<div style='background:{bg}; border-radius:8px; padding:10px; text-align:center'>"
                f"<b>{row['Bucket']}</b><br>"
                f"<span style='color:{color}; font-size:1.1em'>{row['Deviation_Pct']:+.1f}% deviation</span><br>"
                f"<small>Tolerance: ±5%</small>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── Tab 2: Liability cashflows ────────────────────────────────────────────
    with tab_cashflows:
        cf = get_liability_cashflows()
        st.markdown(
            "30-year USD pension cashflow stream ($15M base, 2.5% annual growth) "
            "and present value profile"
        )
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=cf["Year"], y=cf["Cashflow_M"],
            mode="lines", name="Cashflow ($M)",
            line=dict(color="#F39C12", width=2),
            fill="tozeroy", fillcolor="rgba(243,156,18,0.1)",
        ))
        fig2.add_trace(go.Scatter(
            x=cf["Year"], y=cf["PV_M"],
            mode="lines", name="Present Value ($M)",
            line=dict(color="#2E86C1", width=2),
            fill="tozeroy", fillcolor="rgba(46,134,193,0.1)",
        ))
        fig2.update_layout(
            template="plotly_white", height=380,
            margin=dict(t=20, b=20),
            xaxis_title="Year",
            yaxis_ticksuffix="M",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig2, use_container_width=True)

    # ── Tab 3: Yield curve ────────────────────────────────────────────────────
    with tab_yield:
        yc = get_yield_curve(shock_bps)
        shock_label = f"{shock_bps:+d} bps parallel shift applied" if shock_bps != 0 else "No shock applied"
        st.markdown(f"Current US Treasury yield curve — {shock_label}")
        fig3 = px.line(
            yc, x="Tenor", y="Yield_Pct",
            markers=True, template="plotly_white",
            labels={"Yield_Pct": "Yield (%)"},
            color_discrete_sequence=["#27AE60"],
        )
        fig3.update_traces(line_width=2.5, marker_size=8)
        fig3.update_layout(
            height=380, margin=dict(t=20, b=20),
            yaxis_ticksuffix="%",
        )
        st.plotly_chart(fig3, use_container_width=True)

    # ── Tab 4: Sensitivity analysis ───────────────────────────────────────────
    with tab_sensitivity:
        st.markdown("Liability NPV and PV01 across yield shock scenarios")
        sens = sensitivity_table()
        base_npv = calculate_liabilities(0)["npv"]
        styled = sens.copy()
        styled["Liability NPV"] = styled["Liability NPV"].map(lambda x: f"${x/1e6:,.1f}M")
        styled["Liability PV01"] = styled["Liability PV01"].map(lambda x: f"${x:,.0f}")
        styled["NPV Change ($M)"] = styled["NPV Change ($M)"].map(lambda x: f"{x:+,.1f}")
        st.dataframe(styled, hide_index=True, use_container_width=True)

    st.markdown("---")

    # ── Trade execution table ─────────────────────────────────────────────────
    st.subheader("Optimal Trade Execution")
    st.caption("KRD-matched trades per bucket · Each bucket within ±5% of liability PV01")

    trade_df = portfolio[["Instrument", "Type", "Tenor_Yrs", "Optimal_Notional_M", "Allocated_PV01"]].copy()
    total_pv01_sum = trade_df["Allocated_PV01"].sum()
    trade_df["% of Total"] = trade_df["Allocated_PV01"] / total_pv01_sum * 100

    display = trade_df.rename(columns={
        "Tenor_Yrs":          "Tenor (Y)",
        "Optimal_Notional_M": "Buy Notional ($M)",
        "Allocated_PV01":     "PV01 Contribution",
        "% of Total":         "% of Total",
    }).copy()
    display["Buy Notional ($M)"]   = display["Buy Notional ($M)"].map(lambda x: f"${x:,.2f}M")
    display["PV01 Contribution"]   = display["PV01 Contribution"].map(lambda x: f"${x:,.0f}")
    display["% of Total"]          = display["% of Total"].map(lambda x: f"{x:.1f}%")

    st.dataframe(display, hide_index=True, use_container_width=True)

    total_notional_sum = portfolio["Optimal_Notional_M"].sum()
    total_pv01_sum_raw = portfolio["Allocated_PV01"].sum()
    t1, t2, t3 = st.columns(3)
    t1.metric("Total Notional", f"${total_notional_sum:,.2f}M")
    t2.metric("Total PV01",     f"${total_pv01_sum_raw:,.0f}")
    t3.metric("% of Budget",    f"{total_notional_sum / capital_m * 100:.1f}%")

else:
    st.error(
        "**Optimization Infeasible** — insufficient capital to satisfy all KRD bucket "
        "constraints. Try increasing the capital budget."
    )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.info(
    "**Methodology** — Liabilities are modeled as a 30-year USD cash flow leg "
    "($15M × 1.025^t, discounted at a flat 4.45% curve). PV01 is calculated via "
    "a 1bp parallel shift. The optimizer matches each KRD maturity bucket "
    "(Short/Intermediate/Long) independently within ±5% tolerance, "
    "using the most capital-efficient instrument per bucket."
)
