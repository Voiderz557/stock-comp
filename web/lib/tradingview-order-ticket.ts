import type { TradingViewAutoPlan } from "./api";

/** Format a manual Paper Trading bracket ticket; never submits an order. */
export function formatPaperBracketTicket(plan: TradingViewAutoPlan): string | null {
  if (!Number.isInteger(plan.shares) || plan.shares <= 0 || !Number.isFinite(plan.entry_price)
    || !Number.isFinite(plan.stop_watch_price) || !Number.isFinite(plan.take_profit_watch_price)
    || plan.stop_watch_price >= plan.entry_price || plan.take_profit_watch_price <= plan.entry_price) return null;
  return [
    "TRADINGVIEW PAPER TRADING — MANUAL BRACKET TICKET (NOT PLACED)",
    `Ticker: ${plan.ticker}`,
    `Daily-close reference: $${plan.entry_price.toFixed(2)} (${plan.price_as_of}); NOT a live or guaranteed fill`,
    `Entry: BUY ${plan.shares} whole shares after checking the live paper quote`,
    `Paired stop-loss exit: SELL ${plan.shares} shares, stop trigger $${plan.stop_watch_price.toFixed(2)}`,
    `Paired take-profit exit: SELL ${plan.shares} shares, limit $${plan.take_profit_watch_price.toFixed(2)}`,
    "Attach the exits as a linked stop-loss/take-profit bracket, NOT two independent sell orders.",
    "Verify the filled quantity, linked exits, and order status in TradingView Paper Trading.",
    "If the entry price or filled shares differ, recalculate the risk and exits before submitting.",
    "This app cannot place or verify orders. A stop can fill below its trigger after a gap.",
  ].join("\n");
}
