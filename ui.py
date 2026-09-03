import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from config import (
    LONG_MOMENTUM_DAYS,
    MOVING_AVERAGE_DAYS,
    SHORT_MOMENTUM_DAYS,
)


def draw_card(panel, title, value, color):
    """Display one summary value in a simple dashboard card."""
    panel.set_facecolor("#f4f6f8")
    panel.set_xticks([])
    panel.set_yticks([])

    for border in panel.spines.values():
        border.set_color("#d5d9dd")

    panel.text(
        0.5,
        0.68,
        title,
        ha="center",
        va="center",
        fontsize=11,
        color="#555555",
    )
    panel.text(
        0.5,
        0.32,
        value,
        ha="center",
        va="center",
        fontsize=17,
        fontweight="bold",
        color=color,
    )


def show_dashboard(
    ticker,
    data,
    current_price,
    momentum_5d,
    momentum_20d,
    signal,
    score,
):
    """Create and display the stock dashboard."""
    figure, panels = plt.subplot_mosaic(
        [
            ["signal", "price", "momentum_5d", "momentum_20d"],
            ["main_chart", "main_chart", "main_chart", "main_chart"],
            ["volume", "volume", "table", "table"],
        ],
        figsize=(15, 10),
        gridspec_kw={"height_ratios": [0.8, 4, 2]},
    )

    figure.suptitle(
        f"{ticker} Trading Dashboard",
        fontsize=20,
        fontweight="bold",
    )

    signal_colors = {
        "BUY": "#16833b",
        "WAIT": "#b36b00",
        "AVOID": "#b3261e",
    }

    draw_card(
        panels["signal"],
        "Signal",
        f"{signal} ({score}/3)",
        signal_colors[signal],
    )
    draw_card(
        panels["price"],
        "Current Price",
        f"${current_price:,.2f}",
        "#1f4e79",
    )
    draw_card(
        panels["momentum_5d"],
        "5-Day Momentum",
        f"{momentum_5d * 100:+.2f}%",
        "#16833b" if momentum_5d >= 0 else "#b3261e",
    )
    draw_card(
        panels["momentum_20d"],
        "20-Day Momentum",
        f"{momentum_20d * 100:+.2f}%",
        "#16833b" if momentum_20d >= 0 else "#b3261e",
    )

    moving_average_line = data["Close"].rolling(window=20).mean()

    panels["main_chart"].plot(
        data.index,
        data["Close"],
        label="Close Price",
        color="#1f77b4",
        linewidth=1.8,
    )
    panels["main_chart"].plot(
        data.index,
        moving_average_line,
        label="20-Day Moving Average",
        color="#ff7f0e",
        linewidth=1.6,
    )
    panels["main_chart"].set_title("Price and Trend", fontsize=14)
    panels["main_chart"].set_ylabel("Price")
    panels["main_chart"].yaxis.set_major_formatter(
        mticker.StrMethodFormatter("${x:,.2f}")
    )
    panels["main_chart"].legend()
    panels["main_chart"].grid(True, alpha=0.25)

    panels["volume"].bar(
        data.index,
        data["Volume"],
        color="#7aa6c2",
        width=1.0,
    )
    panels["volume"].set_title("Trading Volume", fontsize=13)
    panels["volume"].set_ylabel("Shares")
    panels["volume"].yaxis.set_major_formatter(
        mticker.StrMethodFormatter("{x:,.0f}")
    )
    panels["volume"].grid(axis="y", alpha=0.25)

    date_locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    date_formatter = mdates.ConciseDateFormatter(date_locator)
    panels["main_chart"].xaxis.set_major_locator(date_locator)
    panels["main_chart"].xaxis.set_major_formatter(date_formatter)

    volume_date_locator = mdates.AutoDateLocator(minticks=4, maxticks=7)
    volume_date_formatter = mdates.ConciseDateFormatter(volume_date_locator)
    panels["volume"].xaxis.set_major_locator(volume_date_locator)
    panels["volume"].xaxis.set_major_formatter(volume_date_formatter)

    recent_data = data[["Close", "Volume"]].tail(5)
    table_rows = [
        [f"${row['Close']:,.2f}", f"{int(row['Volume']):,}"]
        for _, row in recent_data.iterrows()
    ]

    panels["table"].set_title("Most Recent Trading Days", fontsize=13)
    panels["table"].axis("off")
    table = panels["table"].table(
        cellText=table_rows,
        colLabels=["Close", "Volume"],
        rowLabels=recent_data.index.strftime("%b %d"),
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.4)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()
