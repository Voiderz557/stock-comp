"use client";

import { ArrowLeft, Check, Plus, Save, ShieldCheck, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { AccountPositionInput, AccountUpdate } from "@/lib/api";
import type { PublicSnapshot } from "@/lib/types";

type DraftPosition = {
  ticker: string;
  side: "LONG" | "SHORT";
  quantity: string;
  cost_mode: "AVERAGE_PRICE" | "EXACT_COST_BASIS";
  cost: string;
};

function positionDraft(position?: PublicSnapshot["positions"][number]): DraftPosition {
  const exact = position?.exact_cost_basis ?? null;
  return {
    ticker: position?.ticker ?? "",
    side: position?.side ?? "LONG",
    quantity: position ? String(position.quantity) : "",
    cost_mode: exact !== null ? "EXACT_COST_BASIS" : "AVERAGE_PRICE",
    cost: exact !== null ? String(exact) : position?.average_price !== null && position?.average_price !== undefined ? String(position.average_price) : "",
  };
}

export function AccountEditor({ snapshot, onSave, onCancel }: {
  snapshot: PublicSnapshot;
  onSave: (account: AccountUpdate) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(snapshot.account?.name ?? "Competition account");
  const [startingCapital, setStartingCapital] = useState(String(snapshot.account?.starting_capital ?? 100000));
  const [cash, setCash] = useState(String(snapshot.portfolio.cash));
  const [snapshotDate, setSnapshotDate] = useState((snapshot.account?.snapshot_as_of ?? snapshot.snapshot_time).slice(0, 10));
  const [positions, setPositions] = useState<DraftPosition[]>(snapshot.positions.map((position) => positionDraft(position)));
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validation = useMemo(() => {
    const errors: string[] = [];
    const parsedStarting = Number(startingCapital);
    const parsedCash = Number(cash);
    if (!name.trim()) errors.push("Account name is required.");
    if (!Number.isFinite(parsedStarting) || parsedStarting < 0) errors.push("Starting capital must be zero or greater.");
    if (!Number.isFinite(parsedCash) || parsedCash < 0) errors.push("Cash must be zero or greater.");
    if (!snapshotDate) errors.push("Snapshot date is required.");
    const keys = new Set<string>();
    positions.forEach((position, index) => {
      const ticker = position.ticker.trim().toUpperCase();
      const quantity = Number(position.quantity);
      const cost = Number(position.cost);
      if (!/^[A-Z0-9.\-]{1,16}$/.test(ticker)) errors.push(`Holding ${index + 1} needs a valid ticker.`);
      if (!Number.isFinite(quantity) || quantity <= 0) errors.push(`Holding ${index + 1} needs a quantity above zero.`);
      if (!position.cost.trim() || !Number.isFinite(cost) || cost < 0) errors.push(`Holding ${index + 1} needs a non-negative cost value.`);
      const key = `${ticker}:${position.side}`;
      if (keys.has(key)) errors.push(`${ticker} ${position.side} appears more than once.`);
      keys.add(key);
    });
    return errors;
  }, [cash, name, positions, snapshotDate, startingCapital]);

  const payload = useMemo<AccountUpdate>(() => ({
    name: name.trim(),
    starting_capital: Number(startingCapital),
    cash: Number(cash),
    snapshot_as_of: `${snapshotDate}T12:00:00.000Z`,
    expected_portfolio_version: snapshot.account?.portfolio_version ?? 0,
    positions: positions.map<AccountPositionInput>((position) => ({
      ticker: position.ticker.trim().toUpperCase(),
      side: position.side,
      quantity: Number(position.quantity),
      average_price: position.cost_mode === "AVERAGE_PRICE" ? Number(position.cost) : null,
      exact_cost_basis: position.cost_mode === "EXACT_COST_BASIS" ? Number(position.cost) : null,
    })),
  }), [cash, name, positions, snapshot.account?.portfolio_version, snapshotDate, startingCapital]);

  function updatePosition(index: number, patch: Partial<DraftPosition>) {
    setPositions((current) => current.map((position, positionIndex) => positionIndex === index ? { ...position, ...patch } : position));
  }

  function changeCostMode(index: number, mode: DraftPosition["cost_mode"]) {
    setPositions((current) => current.map((position, positionIndex) => positionIndex === index && position.cost_mode !== mode
      ? { ...position, cost_mode: mode, cost: "" }
      : position));
  }

  async function save() {
    if (validation.length || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(payload);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "The account snapshot could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  if (reviewing) return (
    <section className="panel standalone-panel account-editor">
      <button type="button" className="icon-button editor-close" onClick={onCancel} aria-label="Close account editor"><X size={20} /></button>
      <div className="editor-progress" aria-label="Account setup progress"><span className="complete"><Check size={14} /> Enter account</span><i /><span className="current">2 Review</span><i /><span>3 Saved</span></div>
      <span className="section-kicker">Check before saving</span><h2>Review your account snapshot</h2>
      <p>Make sure cash, holdings, and cost basis match your competition account. Imported holdings are account state, not trades.</p>
      <div className="review-summary"><div><span>Account</span><strong>{payload.name}</strong></div><div><span>Starting capital</span><strong>${payload.starting_capital.toLocaleString()}</strong></div><div><span>Cash</span><strong>${payload.cash.toLocaleString()}</strong></div><div><span>Snapshot date</span><strong>{snapshotDate}</strong></div><div><span>Holdings</span><strong>{payload.positions.length}</strong></div></div>
      {payload.positions.length > 0 && <div className="table-wrap review-holdings"><table><thead><tr><th>Ticker</th><th>Side</th><th>Quantity</th><th>Cost basis input</th></tr></thead><tbody>{payload.positions.map((position) => <tr key={`${position.ticker}-${position.side}`}><td><strong>{position.ticker}</strong></td><td>{position.side}</td><td>{position.quantity}</td><td>{position.average_price !== null ? `$${position.average_price} average price` : `$${position.exact_cost_basis} exact basis`}</td></tr>)}</tbody></table></div>}
      <div className="snapshot-assurance"><ShieldCheck size={18} /><div><strong>Nothing will be traded.</strong><span>The previous snapshot will remain in history. Saving replaces current account state and makes any older plan stale.</span></div></div>
      <div className="risk-note">Current prices are not inferred from cost basis. Portfolio value and unrealized P&amp;L will remain unavailable until fresh prices are connected.</div>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <div className="editor-actions"><button className="secondary-button" disabled={saving} onClick={() => setReviewing(false)}><ArrowLeft size={16} /> Back</button><button className="primary-button" disabled={saving} onClick={() => void save()}><Save size={16} /> {saving ? "Saving…" : "Save snapshot"}</button></div>
    </section>
  );

  return (
    <section className="panel standalone-panel account-editor">
      <button type="button" className="icon-button editor-close" onClick={onCancel} aria-label="Close account editor"><X size={20} /></button>
      <div className="editor-progress" aria-label="Account setup progress"><span className="current">1 Enter account</span><i /><span>2 Review</span><i /><span>3 Saved</span></div>
      <span className="section-kicker">Paper account setup</span><h2>Add your cash and holdings</h2><p>Copy the numbers from your competition account. This records what you already own—it does not create trades.</p>
      {!snapshot.account?.portfolio_version && <div className="setup-blocker" role="alert"><strong>Saving is temporarily unavailable.</strong><span>The cloud account update has not been installed yet. You can review this form, but no changes can be saved.</span></div>}
      <div className="form-section-heading"><span>Account basics</span><small>All amounts are in USD</small></div>
      <div className="account-fields"><label>Account name<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label><label>Starting capital<input type="number" min="0" step="0.01" value={startingCapital} onChange={(event) => setStartingCapital(event.target.value)} /></label><label>Current cash<input type="number" min="0" step="0.01" value={cash} onChange={(event) => setCash(event.target.value)} /></label><label>Account snapshot date<input type="date" value={snapshotDate} onChange={(event) => setSnapshotDate(event.target.value)} /></label></div>
      <div className="holdings-heading"><div><span className="section-kicker">Holdings</span><h3>Positions you own now</h3><p>Add each ticker once per side. Use either average purchase price or exact total cost basis. Changing cost type clears its amount so you can enter it in the new units.</p></div>{positions.length > 0 && <button type="button" className="secondary-button" onClick={() => setPositions((current) => [...current, positionDraft()])}><Plus size={16} /> Add another</button>}</div>
      {positions.length === 0 && <div className="holdings-empty"><BriefcaseIcon /><strong>No holdings added</strong><span>If the account is cash-only, you can continue. Otherwise, add the positions you currently own.</span><button type="button" className="primary-button" onClick={() => setPositions([positionDraft()])}><Plus size={16} /> Add first holding</button></div>}
      <div className="holding-editor-list">{positions.map((position, index) => <div className="holding-editor-row" key={index}><label>Ticker<input value={position.ticker} maxLength={16} onChange={(event) => updatePosition(index, { ticker: event.target.value.toUpperCase() })} /></label><label>Side<select value={position.side} onChange={(event) => updatePosition(index, { side: event.target.value as "LONG" | "SHORT" })}><option>LONG</option><option>SHORT</option></select></label><label>Quantity<input type="number" min="0" step="any" value={position.quantity} onChange={(event) => updatePosition(index, { quantity: event.target.value })} /></label><label>Cost type<select value={position.cost_mode} onChange={(event) => changeCostMode(index, event.target.value as DraftPosition["cost_mode"])}><option value="AVERAGE_PRICE">Average price</option><option value="EXACT_COST_BASIS">Exact cost basis</option></select></label><label>{position.cost_mode === "AVERAGE_PRICE" ? "Average price" : "Exact cost basis"}<input type="number" min="0" step="0.01" value={position.cost} onChange={(event) => updatePosition(index, { cost: event.target.value })} /></label><button className="icon-button remove-holding" aria-label={`Remove holding ${index + 1}`} onClick={() => setPositions((current) => current.filter((_, positionIndex) => positionIndex !== index))}><Trash2 size={17} /></button></div>)}</div>
      {validation.length > 0 && <div className="validation-list" role="alert">{validation.map((item) => <span key={item}>{item}</span>)}</div>}
      <div className="editor-footer"><div><strong>{positions.length} holding{positions.length === 1 ? "" : "s"}</strong><span> · ${Number(cash || 0).toLocaleString()} cash</span></div><div className="editor-actions"><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button><button type="button" className="primary-button" disabled={validation.length > 0 || !snapshot.account?.portfolio_version} onClick={() => setReviewing(true)}>Review changes</button></div></div>
    </section>
  );
}

function BriefcaseIcon() {
  return <span className="holdings-empty-icon" aria-hidden="true">$</span>;
}
