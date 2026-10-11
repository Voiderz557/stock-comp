import assert from "node:assert/strict";
import test from "node:test";
import { formatPaperBracketTicket } from "../lib/tradingview-order-ticket.ts";

const plan = {
  ticker: "TEST", price_as_of: "2026-10-08", entry_price: 100,
  stop_watch_price: 94, take_profit_watch_price: 112, shares: 12,
};

test("manual ticket clearly states linked paper exits and does not claim placement", () => {
  const ticket = formatPaperBracketTicket(plan);
  assert.match(ticket, /NOT PLACED/);
  assert.match(ticket, /BUY 12 whole shares/);
  assert.match(ticket, /Paired stop-loss exit: SELL 12 shares, stop trigger \$94\.00/);
  assert.match(ticket, /Paired take-profit exit: SELL 12 shares, limit \$112\.00/);
  assert.match(ticket, /NOT two independent sell orders/);
  assert.match(ticket, /cannot place or verify orders/);
});

test("invalid levels or zero-share suggestion cannot produce a ticket", () => {
  for (const changed of [
    { shares: 0 }, { shares: -1 }, { shares: 0.5 },
    { stop_watch_price: 100 }, { take_profit_watch_price: 100 },
    { entry_price: Number.NaN },
  ]) assert.equal(formatPaperBracketTicket({ ...plan, ...changed }), null);
});
