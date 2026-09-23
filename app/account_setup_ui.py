"""Streamlit form for a manual competition-account snapshot."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from paper_trading.account_setup import (
    AccountSetupError,
    apply_account_snapshot,
    parse_holdings_text,
    preview_account_snapshot,
)


def render_account_setup_section(db_path=None):
    st.header("Set up / update competition account")
    st.caption(
        "Enter the live competition account as a snapshot. This replaces "
        "current cash and open holdings. It does not record fake trades, "
        "invent realized P&L, or delete closed-trade history. Review the "
        "preview before saving."
    )

    with st.form("competition_account_setup"):
        columns = st.columns(3)
        starting_capital = columns[0].number_input(
            "Starting capital",
            min_value=0.0,
            value=100_000.0,
            step=1000.0,
            format="%.2f",
        )
        cash = columns[1].number_input(
            "Current cash",
            min_value=0.0,
            value=100_000.0,
            step=100.0,
            format="%.2f",
        )
        snapshot_as_of = columns[2].date_input("Account snapshot date", value=date.today())
        holdings_text = st.text_area(
            "Holdings",
            height=140,
            help=(
                "One holding per line: ticker, LONG or SHORT, quantity, average price. "
                "To use exact cost basis instead, leave average price blank and add "
                "the cost as a fifth value. Duplicate ticker+direction rows are rejected."
            ),
            placeholder="AAPL, LONG, 10, 150\nMSFT, LONG, 5, , 2000",
        )
        preview_clicked = st.form_submit_button("Review snapshot")

    if preview_clicked:
        try:
            holdings = parse_holdings_text(holdings_text)
            st.session_state["account_setup_preview"] = preview_account_snapshot(
                starting_capital,
                cash,
                holdings,
                snapshot_as_of.isoformat(),
                db_path=db_path,
            )
        except AccountSetupError as error:
            st.session_state.pop("account_setup_preview", None)
            st.error(str(error))
            return

    preview = st.session_state.get("account_setup_preview")
    if not preview:
        return

    st.subheader("Review before saving")
    st.write(
        f"Snapshot date **{preview['snapshot_as_of']}**. "
        f"Starting capital ${preview['starting_capital']:,.2f}. "
        f"Cash ${preview['cash']:,.2f}. "
        f"Holdings cost basis ${preview['allocated_capital']:,.2f}."
    )
    st.caption(
        f"Existing closed trades kept: {preview['preserved_closed_trades']}. "
        f"Stored realized P&L kept: ${preview['preserved_realized_pnl']:,.2f}. "
        f"Open lots that will be replaced: {preview['replaced_open_lots']}."
    )
    if preview["holdings"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Ticker": row["ticker"],
                        "Direction": row["direction"],
                        "Quantity": row["quantity"],
                        "Average price": row["entry_price"],
                        "Cost basis": row["allocated_capital"],
                    }
                    for row in preview["holdings"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No holdings in this snapshot. Cash-only is allowed if identity holds.")
    for note in preview["notes"]:
        st.caption(note)

    if preview["missing_information"]:
        for message in preview["missing_information"]:
            st.error(message)
        return
    if preview["already_matches"]:
        st.info("This snapshot already matches the saved account. Saving again will not add duplicate holdings.")
        return

    confirm = st.button("Confirm and save account snapshot")
    if confirm:
        try:
            result = apply_account_snapshot(preview, db_path=db_path)
        except AccountSetupError as error:
            st.error(str(error))
            return
        st.session_state.pop("account_setup_preview", None)
        st.success(result["message"])
        st.rerun()
