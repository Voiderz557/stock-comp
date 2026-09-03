"""Dated Nasdaq-100 universes used only by historical backtests.

The annual snapshots are reconstructed point-in-time memberships based on the
Nasdaq change record compiled by thuningxu/sp500nq100.  That record works
backward from the current constituent table and applies dated changes.  Nasdaq
announcements were used to verify the recent annual and off-cycle changes.

These are research-quality approximations, not a licensed Nasdaq constituent
feed.  Keeping the snapshots here makes their dates and contents auditable.
"""

from bisect import bisect_right
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class UniverseSnapshot:
    effective_date: pd.Timestamp
    tickers: tuple[str, ...]
    approximate: bool = True


def _tickers(value):
    return tuple(value.split(","))


_ANNUAL_NASDAQ_100_SNAPSHOTS = (
    UniverseSnapshot(pd.Timestamp("2021-12-20"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALGN,AMAT,AMD,AMGN,AMZN,ANSS,ASML,ATVI,AVGO,BIDU,BIIB,BKNG,CDNS,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSX,CTAS,CTSH,DDOG,DLTR,DOCU,DXCM,EA,EBAY,EXC,FAST,FB,FISV,FTNT,GILD,GOOG,GOOGL,HON,IDXX,ILMN,INTC,INTU,ISRG,JD,KDP,KHC,KLAC,LCID,LRCX,LULU,MAR,MCHP,MDLZ,MELI,MNST,MRNA,MRVL,MSFT,MTCH,MU,NFLX,NTES,NVDA,NXPI,OKTA,ORLY,PANW,PAYX,PCAR,PDD,PEP,PTON,PYPL,QCOM,REGN,ROST,SBUX,SGEN,SIRI,SNPS,SPLK,SWKS,TEAM,TMUS,TSLA,TXN,VRSK,VRSN,VRTX,WBA,WDAY,XEL,XLNX,ZM,ZS"
    )),
    UniverseSnapshot(pd.Timestamp("2022-12-19"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALGN,AMAT,AMD,AMGN,AMZN,ANSS,ASML,ATVI,AVGO,AZN,BIIB,BKNG,BKR,CDNS,CEG,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSGP,CSX,CTAS,CTSH,DDOG,DLTR,DXCM,EA,EBAY,ENPH,EXC,FANG,FAST,FISV,FTNT,GFS,GILD,GOOG,GOOGL,HON,IDXX,ILMN,INTC,INTU,ISRG,JD,KDP,KHC,KLAC,LCID,LRCX,LULU,MAR,MCHP,MDLZ,MELI,META,MNST,MRNA,MRVL,MSFT,MU,NFLX,NVDA,NXPI,ODFL,ORLY,PANW,PAYX,PCAR,PDD,PEP,PYPL,QCOM,REGN,RIVN,ROST,SBUX,SGEN,SIRI,SNPS,TEAM,TMUS,TSLA,TXN,VRSK,VRTX,WBA,WBD,WDAY,XEL,ZM,ZS"
    )),
    UniverseSnapshot(pd.Timestamp("2023-12-18"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,AMAT,AMD,AMGN,AMZN,ANSS,ASML,AVGO,AZN,BIIB,BKNG,BKR,CCEP,CDNS,CDW,CEG,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSGP,CSX,CTAS,CTSH,DASH,DDOG,DLTR,DXCM,EA,EXC,FANG,FAST,FTNT,GEHC,GFS,GILD,GOOG,GOOGL,HON,IDXX,ILMN,INTC,INTU,ISRG,KDP,KHC,KLAC,LRCX,LULU,MAR,MCHP,MDB,MDLZ,MELI,META,MNST,MRNA,MRVL,MSFT,MU,NFLX,NVDA,NXPI,ODFL,ON,ORLY,PANW,PAYX,PCAR,PDD,PEP,PYPL,QCOM,REGN,ROP,ROST,SBUX,SIRI,SNPS,SPLK,TEAM,TMUS,TSLA,TTD,TTWO,TXN,VRSK,VRTX,WBA,WBD,WDAY,XEL,ZS"
    )),
    UniverseSnapshot(pd.Timestamp("2024-12-23"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,AMAT,AMD,AMGN,AMZN,ANSS,APP,ARM,ASML,AVGO,AXON,AZN,BIIB,BKNG,BKR,CCEP,CDNS,CDW,CEG,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSGP,CSX,CTAS,CTSH,DASH,DDOG,DXCM,EA,EXC,FANG,FAST,FTNT,GEHC,GFS,GILD,GOOG,GOOGL,HON,IDXX,INTC,INTU,ISRG,KDP,KHC,KLAC,LIN,LRCX,LULU,MAR,MCHP,MDB,MDLZ,MELI,META,MNST,MRVL,MSFT,MSTR,MU,NFLX,NVDA,NXPI,ODFL,ON,ORLY,PANW,PAYX,PCAR,PDD,PEP,PLTR,PYPL,QCOM,REGN,ROP,ROST,SBUX,SNPS,TEAM,TMUS,TSLA,TTD,TTWO,TXN,VRSK,VRTX,WBD,WDAY,XEL,ZS"
    )),
    UniverseSnapshot(pd.Timestamp("2025-12-22"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALNY,AMAT,AMD,AMGN,AMZN,APP,ARM,ASML,AVGO,AXON,AZN,BKNG,BKR,CCEP,CDNS,CEG,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSGP,CSX,CTAS,CTSH,DASH,DDOG,DXCM,EA,EXC,FANG,FAST,FER,FTNT,GEHC,GILD,GOOG,GOOGL,HON,IDXX,INSM,INTC,INTU,ISRG,KDP,KHC,KLAC,LIN,LRCX,MAR,MCHP,MDLZ,MELI,META,MNST,MPWR,MRVL,MSFT,MSTR,MU,NFLX,NVDA,NXPI,ODFL,ORLY,PANW,PAYX,PCAR,PDD,PEP,PLTR,PYPL,QCOM,REGN,ROP,ROST,SBUX,SHOP,SNPS,STX,TEAM,TMUS,TRI,TSLA,TTWO,TXN,VRSK,VRTX,WBD,WDAY,WDC,XEL,ZS"
    )),
    UniverseSnapshot(pd.Timestamp("2026-05-18"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALNY,AMAT,AMD,AMGN,AMZN,APP,ARM,ASML,AVGO,AXON,BKNG,BKR,CCEP,CDNS,CEG,CHTR,CMCSA,COST,CPRT,CRWD,CSCO,CSX,CTAS,CTSH,DASH,DDOG,DXCM,EA,EXC,FANG,FAST,FER,FTNT,GEHC,GILD,GOOG,GOOGL,HON,IDXX,INSM,INTC,INTU,ISRG,KDP,KHC,KLAC,LIN,LITE,LRCX,MAR,MCHP,MDLZ,MELI,META,MNST,MPWR,MRVL,MSFT,MSTR,MU,NFLX,NVDA,NXPI,ODFL,ORLY,PANW,PAYX,PCAR,PDD,PEP,PLTR,PYPL,QCOM,REGN,ROP,ROST,SBUX,SHOP,SNDK,SNPS,STX,TMUS,TRI,TSLA,TTWO,TXN,VRSK,VRTX,WBD,WDAY,WDC,WMT,XEL,ZS"
    )),
    # Official Nasdaq June 2026 quarterly changes applied to the May snapshot.
    UniverseSnapshot(pd.Timestamp("2026-06-22"), _tickers(
        "AAPL,ABNB,ADBE,ADI,ADP,ADSK,AEP,ALAB,ALNY,AMAT,AMD,AMGN,AMZN,APP,ARM,ASML,AVGO,AXON,BKNG,BKR,CCEP,CDNS,CEG,CMCSA,COST,CPRT,CRWD,CRWV,CSCO,CSX,CTAS,DASH,DDOG,DXCM,EA,EXC,FANG,FAST,FER,FTNT,GEHC,GILD,GOOG,GOOGL,HON,IDXX,INTC,INTU,ISRG,KDP,KHC,KLAC,LIN,LITE,LRCX,MAR,MCHP,MDLZ,MELI,META,MNST,MPWR,MRVL,MSFT,MSTR,MU,NBIS,NFLX,NVDA,NXPI,ODFL,ORLY,PANW,PAYX,PCAR,PDD,PEP,PLTR,PYPL,QCOM,REGN,RKLB,ROP,ROST,SBUX,SHOP,SNDK,SNPS,STX,TER,TMUS,TRI,TSLA,TTWO,TXN,VRTX,WBD,WDAY,WDC,WMT,XEL"
    )),
)

# Off-cycle changes between the annual anchors. These make modern membership
# closer to daily point-in-time data while retaining explicit dated snapshots.
_OFF_CYCLE_CHANGES = (
    ("2022-01-24", ("ODFL",), ("PTON",)),
    ("2022-02-02", ("CEG",), ()),
    ("2022-02-22", ("AZN",), ("XLNX",)),
    ("2022-06-09", ("META",), ("FB",)),
    ("2022-11-21", ("ENPH",), ("OKTA",)),
    ("2023-06-07", ("GEHC",), ("FISV",)),
    ("2023-06-20", ("ON",), ("RIVN",)),
    ("2023-07-17", ("TTD",), ("ATVI",)),
    ("2023-12-14", ("TTWO",), ("SGEN",)),
    ("2024-03-18", ("LIN",), ("SPLK",)),
    ("2024-06-24", ("ARM",), ("SIRI",)),
    ("2024-07-22", ("SMCI",), ("WBA",)),
    ("2024-11-18", ("APP",), ("DLTR",)),
    ("2025-05-19", ("SHOP",), ("MDB",)),
    ("2025-07-17", (), ("ANSS",)),
    ("2025-07-28", ("TRI",), ()),
    ("2025-10-30", ("SOLS",), ()),
    ("2025-11-06", (), ("SOLS",)),
    ("2026-01-05", ("VSNT",), ()),
    ("2026-01-09", (), ("VSNT",)),
    ("2026-01-20", ("WMT",), ("AZN",)),
    ("2026-04-20", ("SNDK",), ("TEAM",)),
)


def _build_snapshots():
    anchors = {
        snapshot.effective_date: snapshot
        for snapshot in _ANNUAL_NASDAQ_100_SNAPSHOTS
    }
    changes = {
        pd.Timestamp(date): (added, removed)
        for date, added, removed in _OFF_CYCLE_CHANGES
    }
    dates = sorted(set(anchors) | set(changes))
    members = set()
    snapshots = []
    for date in dates:
        if date in anchors:
            members = set(anchors[date].tickers)
        else:
            added, removed = changes[date]
            members.difference_update(removed)
            members.update(added)
        snapshots.append(UniverseSnapshot(date, tuple(sorted(members))))
    return tuple(snapshots)


HISTORICAL_NASDAQ_100_SNAPSHOTS = _build_snapshots()

SNAPSHOT_DATES = tuple(
    snapshot.effective_date for snapshot in HISTORICAL_NASDAQ_100_SNAPSHOTS
)

UNIVERSE_SOURCE = (
    "Dated snapshots reconstructed from the Wikipedia Nasdaq-100 component "
    "change table by thuningxu/sp500nq100 and checked against recent official "
    "Nasdaq change announcements. This is approximate, not a licensed daily feed."
)


def get_historical_universe(date):
    """Return the newest snapshot effective on or before ``date``."""
    date = pd.Timestamp(date).normalize()
    position = bisect_right(SNAPSHOT_DATES, date) - 1
    if position < 0:
        raise ValueError(
            f"Historical Nasdaq-100 membership is unavailable before "
            f"{SNAPSHOT_DATES[0].date()}."
        )
    return HISTORICAL_NASDAQ_100_SNAPSHOTS[position]


def get_backtest_tickers(start_date, end_date):
    """Return every ticker appearing in a snapshot active in the range."""
    start_date = pd.Timestamp(start_date).normalize()
    end_date = pd.Timestamp(end_date).normalize()
    active = [get_historical_universe(start_date)]
    active.extend(
        snapshot
        for snapshot in HISTORICAL_NASDAQ_100_SNAPSHOTS
        if start_date < snapshot.effective_date <= end_date
    )
    return sorted({ticker for snapshot in active for ticker in snapshot.tickers})
