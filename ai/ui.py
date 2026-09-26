"""Read-only Streamlit section for advisory analysis."""

from __future__ import annotations

import streamlit as st

from ai.analyze import analyze_question, describe_configuration
from paper_trading.analysis_context import build_analysis_context


def render_ai_analysis_section(db_path=None, current_prices=None, price_quotes=None):
    st.header("AI analysis")
    config = describe_configuration()
    st.caption(
        "Read-only and advisory. The model cannot record trades, dismiss "
        "recommendations, or change the ledger. Click Analyze to send the "
        f"snapshot. Provider: `{config['provider']}` · model: `{config['model']}`."
    )
    if not config["configured"] and config["provider"] == "none":
        st.info(
            "No provider is configured. Set STOCK_COMP_AI_PROVIDER and "
            "STOCK_COMP_AI_API_KEY (or OPENAI_API_KEY) to enable a live reply."
        )

    try:
        context = build_analysis_context(
            db_path=db_path,
            current_prices=current_prices,
            price_quotes=price_quotes,
        )
    except Exception as error:
        st.warning(f"Could not build the analysis snapshot: {error}")
        return

    question = st.text_area(
        "Question",
        value="What should I pay attention to today?",
        help="Asked only after you click Analyze. Ordinary reruns do not call the provider.",
    )
    with st.expander("Data sent to AI", expanded=False):
        st.caption(
            "This is the structured snapshot that Analyze will send. "
            "It excludes API keys, local paths, and extra account identifiers. "
            f"Provider `{config['provider']}` / model `{config['model']}`."
        )
        st.json(context)

    analyze_clicked = st.button("Analyze current state")
    if analyze_clicked:
        st.session_state["ai_analysis_result"] = analyze_question(
            question, context
        ).as_dict()

    result = st.session_state.get("ai_analysis_result")
    if not result:
        st.caption("No analysis yet. Click Analyze current state to request one.")
        return

    st.subheader("AI interpretation")
    st.caption(
        "This block is model interpretation, not a recorded fact or an algorithm signal."
    )
    if result.get("ok"):
        st.write(result.get("text") or "")
        if result.get("truncated"):
            st.caption("The reply was truncated to the response size limit.")
    else:
        code = result.get("error_code") or "provider"
        message = result.get("error_message") or "The analysis request failed."
        st.error(f"{code}: {message}")
    st.caption(
        f"Provider `{result.get('provider')}` · model `{result.get('model')}`. "
        "Tests do not guarantee the model always follows instructions."
    )
