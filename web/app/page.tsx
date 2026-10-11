"use client";

import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bell,
  BookOpen,
  BriefcaseBusiness,
  Check,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Compass,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  OctagonAlert,
  Settings,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  UserRound,
  WalletCards,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { LegalLinks } from "@/components/legal-links";
import { GoogleSignIn } from "@/components/google-sign-in";
import { AccountEditor } from "@/components/account-editor";
import { AccountNotFoundError, buildDailyPlan, changeRecommendationStatus, createAccount, loadAccountArchive, loadPaperActions, loadPlanHistory, loadPlanningProfiles, loadSnapshot, loadStrategies, loadTradingViewAutoPlan, loadVixContext, demoMode, recordPaperAction, savePlanningProfile, updateAccount } from "@/lib/api";
import type { AccountUpdate, PlanningProfileInput, TradingViewAutoPlan, VixContext } from "@/lib/api";
import { DEFAULT_COMPETITION_PROFILE, effectiveMinimumPrice, managedAppSettingsProfile, resolvePlanningSettings, savedAppSettingsProfile } from "@/lib/planning-settings";
import { DEMO_SNAPSHOT } from "@/lib/demo-data";
import { compareSavedPlans } from "@/lib/plan-comparison";
import { getOAuthAvailability, getSupabaseClient, sendMagicLink, startOAuth, supabaseConfigured } from "@/lib/supabase";
import type { CompetitionPlanningRules, PaperAction, PlanHistoryItem, PlanningProfile, PublicSnapshot, Recommendation } from "@/lib/types";

const nav = [
  { label: "Overview", icon: LayoutDashboard },
  { label: "Today's plan", icon: Compass },
  { label: "Portfolio", icon: BriefcaseBusiness },
  { label: "History", icon: History },
  { label: "Research", icon: BookOpen },
];

const PLAN_MARKET_MAX_AGE_MS = 4 * 24 * 60 * 60 * 1000;

/** Daily bars carry a session date at UTC midnight, not a local wall-clock quote. */
function marketSessionDate(value: string | null | undefined): string {
  const session = value?.slice(0, 10);
  return session && /^\d{4}-\d{2}-\d{2}$/.test(session) ? session : "unavailable";
}

function isPlanActionable(plan: PublicSnapshot["plan"], now: number): boolean {
  if (plan.status !== "CURRENT" || !plan.generated_at || !plan.market_as_of) return false;
  const marketTime = Date.parse(plan.market_as_of);
  return Number.isFinite(marketTime) && marketTime <= now && marketTime >= now - PLAN_MARKET_MAX_AGE_MS;
}

const googleDirectConfigured = Boolean(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim());

function Logo() {
  return (
    <div className="logo" aria-label="Stock Competition">
      <span className="logo-mark"><TrendingUp size={19} /></span>
      <span><strong>Stock</strong> Competition</span>
    </div>
  );
}

function SignIn({
  onDemo,
  onOAuth,
  onMagicLink,
  onGoogleError,
  googleReady,
  microsoftReady,
  magicReady,
  demoReady,
  error,
}: {
  onDemo: () => void;
  onOAuth: (provider: "google" | "azure") => void;
  onMagicLink: (email: string) => Promise<void>;
  onGoogleError: (message: string) => void;
  googleReady: boolean;
  microsoftReady: boolean;
  magicReady: boolean;
  demoReady: boolean;
  error: string | null;
}) {
  const [email, setEmail] = useState("");
  const [magicStatus, setMagicStatus] = useState<string | null>(null);
  const [sendingMagic, setSendingMagic] = useState(false);

  async function submitMagicLink(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSendingMagic(true);
    setMagicStatus(null);
    try {
      await onMagicLink(email.trim());
      setMagicStatus("Check your email for the secure sign-in link.");
    } catch (magicError) {
      setMagicStatus(magicError instanceof Error ? magicError.message : "Email sign in could not be started.");
    } finally {
      setSendingMagic(false);
    }
  }

  return (
    <main className="signin-shell">
      <div className="signin-grid">
        <section className="signin-story">
          <Logo />
          <div className="story-copy">
            <span className="eyebrow">A calmer way to compete</span>
            <h1>Know what matters before the market moves.</h1>
            <p>Bring your portfolio, daily plan, algorithm signals, and paper-trading history into one focused decision surface.</p>
          </div>
          <div className="market-ribbon">
            <span><i className="status-dot" /> Research preview</span>
            <strong>Paper trading only</strong>
            <span>No market data shown before sign-in</span>
          </div>
        </section>
        <section className="signin-panel">
          <div className="signin-card">
            <span className="eyebrow">Private preview</span>
            <h2>Welcome back</h2>
            <p>{googleReady || microsoftReady ? "Sign in to open your private competition account." : magicReady ? "Use a secure email link to open your private competition account." : "Authentication is not configured yet. Use the isolated sample account."}</p>
            {googleReady && googleDirectConfigured && <GoogleSignIn onError={onGoogleError} />}
            {googleReady && googleDirectConfigured ? (
              <button className="text-button oauth-fallback" onClick={() => onOAuth("google")}>Use Google redirect instead</button>
            ) : (
              <button className="oauth-button" onClick={() => onOAuth("google")} disabled={!googleReady}>
                <span className="provider-mark">G</span> Continue with Google
              </button>
            )}
            {googleReady && !googleDirectConfigured && <p className="auth-provider-note">The Google redirect may show our Supabase authentication domain. Check that the address begins with your project&apos;s trusted Supabase URL.</p>}
            <button className="oauth-button" onClick={() => onOAuth("azure")} disabled={!microsoftReady}>
              <span className="provider-mark microsoft">M</span> Continue with Microsoft
            </button>
            {!microsoftReady && supabaseConfigured && <p className="auth-provider-note">Microsoft sign-in is not configured yet. You can use Google or an email link for now.</p>}
            {error && <p className="auth-error" role="alert">{error}</p>}
            <div className="divider"><span>email sign in</span></div>
            <form className="magic-form" onSubmit={submitMagicLink}>
              <label htmlFor="signin-email">Email address</label>
              <div>
                <input id="signin-email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" disabled={!magicReady || sendingMagic} />
                <button className="secondary-button" type="submit" disabled={!magicReady || sendingMagic}>{sendingMagic ? "Sending…" : "Email me a link"}</button>
              </div>
              {magicStatus && <p className="magic-status" role="status">{magicStatus}</p>}
            </form>
            <div className="divider"><span>prototype access</span></div>
            <button className="primary-button full" onClick={onDemo} disabled={!demoReady}>Open demo account <ArrowRight size={17} /></button>
            <p className="fine-print"><ShieldCheck size={14} /> Demo mode never reads your local portfolio database.</p>
            <LegalLinks />
          </div>
        </section>
      </div>
    </main>
  );
}

function AccountSetup({
  onSave,
  onSignOut,
  error,
}: {
  onSave: (account: { name: string; starting_capital: number; cash: number; snapshot_as_of: string }) => Promise<void>;
  onSignOut: () => void;
  error: string | null;
}) {
  const [name, setName] = useState("Competition account");
  const [startingCapital, setStartingCapital] = useState("100000");
  const [cash, setCash] = useState("100000");
  const [snapshotDate, setSnapshotDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);

  const parsedStartingCapital = Number(startingCapital);
  const parsedCash = Number(cash);
  const valid = name.trim().length > 0
    && Number.isFinite(parsedStartingCapital) && parsedStartingCapital >= 0
    && Number.isFinite(parsedCash) && parsedCash >= 0
    && Boolean(snapshotDate);

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        starting_capital: parsedStartingCapital,
        cash: parsedCash,
        snapshot_as_of: `${snapshotDate}T12:00:00.000Z`,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="signin-shell">
      <div className="setup-wrap">
        <section className="setup-card">
          <Logo />
          <span className="eyebrow">First-time setup</span>
          <h1>{reviewing ? "Review your account" : "Create your competition account"}</h1>
          <p>This establishes your cloud account. You can add holdings after setup; no real trades are placed.</p>
          {!reviewing ? (
            <form className="setup-form" onSubmit={(event) => { event.preventDefault(); if (valid) setReviewing(true); }}>
              <label>Account name<input value={name} maxLength={120} required onChange={(event) => setName(event.target.value)} /></label>
              <div className="setup-grid">
                <label>Starting capital<input type="number" min="0" step="0.01" required value={startingCapital} onChange={(event) => setStartingCapital(event.target.value)} /></label>
                <label>Current cash<input type="number" min="0" step="0.01" required value={cash} onChange={(event) => setCash(event.target.value)} /></label>
              </div>
              <label>Account snapshot date<input type="date" required value={snapshotDate} onChange={(event) => setSnapshotDate(event.target.value)} /></label>
              {error && <p className="auth-error" role="alert">{error}</p>}
              <button
                className="primary-button full"
                type="button"
                disabled={!valid}
                onClick={() => setReviewing(true)}
              >
                Review account <ArrowRight size={17} />
              </button>
            </form>
          ) : (
            <div className="setup-review">
              <div><span>Account</span><strong>{name.trim()}</strong></div>
              <div><span>Starting capital</span><strong>{money(parsedStartingCapital)}</strong></div>
              <div><span>Current cash</span><strong>{money(parsedCash)}</strong></div>
              <div><span>Snapshot date</span><strong>{new Date(`${snapshotDate}T12:00:00Z`).toLocaleDateString()}</strong></div>
              <p><ShieldCheck size={16} /> Saving twice will return the same account, not create a duplicate.</p>
              {error && <p className="auth-error" role="alert">{error}</p>}
              <div className="setup-actions">
                <button className="secondary-button" type="button" disabled={saving} onClick={() => setReviewing(false)}>Edit</button>
                <button className="primary-button" type="button" disabled={saving} onClick={() => void save()}>{saving ? "Saving…" : "Create account"}</button>
              </div>
            </div>
          )}
          <button className="text-button setup-signout" onClick={onSignOut}><LogOut size={15} /> Sign out</button>
          <LegalLinks />
        </section>
      </div>
    </main>
  );
}

function MetricCard({ label, value, detail, trend }: { label: string; value: string; detail: string; trend?: "up" | "down" }) {
  return (
    <article className="metric-card">
      <span className="metric-label">{label}</span>
      <div className="metric-value">{value}</div>
      <span className={`metric-detail ${trend ?? ""}`}>
        {trend === "up" && <ArrowUpRight size={14} />}
        {trend === "down" && <ArrowDownRight size={14} />}
        {detail}
      </span>
    </article>
  );
}

function money(value: number | null, signed = false): string {
  if (value === null) return "Unavailable";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value)}`;
}

function scoreMetric(value: number | null): { label: string; width: string } {
  return value === null
    ? { label: "N/A", width: "0%" }
    : { label: value.toFixed(0), width: `${Math.max(0, Math.min(100, value))}%` };
}

function detailValue(value: unknown): string {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return Number.isFinite(value) ? String(Number(value.toFixed(5))) : "Unavailable";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function proposedShares(item: Recommendation): number | null {
  const value = item.factor_details?.["Proposed Quantity"];
  return item.action === "BUY" && typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function proposedAllocation(item: Recommendation): number | null {
  const value = item.factor_details?.["Proposed Allocation"];
  return item.action === "BUY" && typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function AppShell({
  onSignOut,
  onUpdateAccount,
  onBuildPlan,
  onRecommendationEvent,
  onExportAccount,
  onRecordPaperAction,
  strategies,
  history,
  historyError,
  paperActions,
  paperActionsAvailable,
  paperActionsError,
  snapshot,
  source,
  vix,
  savedSettings,
  settingsLoaded,
  onSaveSettings,
  settingsError,
  onStopPreview,
}: {
  onSignOut: () => void;
  onUpdateAccount: (account: AccountUpdate) => Promise<void>;
  onBuildPlan: (strategy: string, targetPositionValue: number, minimumPrice: number, universeMode: "EXPANDED" | "BROAD") => Promise<void>;
  onRecommendationEvent: (id: string, eventType: "DISMISSED" | "REOPENED") => Promise<void>;
  onExportAccount: () => Promise<void>;
  onRecordPaperAction: (recommendationId: string, quantity: number, expectedVersion: number, idempotencyKey: string) => Promise<void>;
  strategies: string[];
  history: PlanHistoryItem[];
  historyError: string | null;
  paperActions: PaperAction[];
  paperActionsAvailable: boolean;
  paperActionsError: string | null;
  snapshot: PublicSnapshot;
  source: "demo" | "api";
  vix: VixContext | null;
  savedSettings: PlanningProfile | null;
  settingsLoaded: boolean;
  onSaveSettings: (rules: CompetitionPlanningRules) => Promise<void>;
  settingsError: string | null;
  onStopPreview: (input: { ticker: string; available_cash: number; account_equity: number }) => Promise<TradingViewAutoPlan>;
}) {
  const [active, setActive] = useState("Overview");
  const [menuOpen, setMenuOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [showDismissed, setShowDismissed] = useState(false);
  const [recommendationError, setRecommendationError] = useState<string | null>(null);
  const [recommendationBusy, setRecommendationBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Recommendation | null>(null);
  const [paperAction, setPaperAction] = useState<Recommendation | null>(null);
  const [paperQuantity, setPaperQuantity] = useState(0);
  const [paperActionKey, setPaperActionKey] = useState<string | null>(null);
  const [paperActionBusy, setPaperActionBusy] = useState(false);
  const [paperActionError, setPaperActionError] = useState<string | null>(null);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const settings = resolvePlanningSettings((savedSettings?.rules ?? DEFAULT_COMPETITION_PROFILE.rules) as CompetitionPlanningRules);
  const [planStrategy, setPlanStrategy] = useState(settings.strategy);
  const [universeMode, setUniverseMode] = useState(settings.universe_mode);
  const [targetPositionValue, setTargetPositionValue] = useState(settings.target_position_value);
  const [minimumPriceMode, setMinimumPriceMode] = useState(settings.minimum_price_mode);
  const [minimumPrice, setMinimumPrice] = useState(String(settings.minimum_price));
  const [stopLossEnabled, setStopLossEnabled] = useState(settings.stop_loss_enabled);
  const [takeProfitEnabled, setTakeProfitEnabled] = useState(settings.take_profit_enabled);
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsSaveError, setSettingsSaveError] = useState<string | null>(null);
  const [settingsSaved, setSettingsSaved] = useState(false);
  const [exitPreview, setExitPreview] = useState<TradingViewAutoPlan | null>(null);
  const [exitPreviewError, setExitPreviewError] = useState<string | null>(null);
  const [buildingPlan, setBuildingPlan] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const hasPositions = snapshot.positions.length > 0;
  const hasPlan = Boolean(snapshot.plan.generated_at);
  const planActionable = isPlanActionable(snapshot.plan, now);
  const planDateStale = snapshot.plan.status === "CURRENT" && !planActionable;
  const planStatus = planDateStale ? "STALE — market date" : snapshot.plan.status;
  const planDateWarning = planDateStale
    ? "This plan's market date is missing, invalid, in the future, or older than four days. Build a fresh plan before using its recommendations or recording paper actions."
    : null;
  const paperActionBlocker = snapshot.plan.warnings.find((warning) => warning.startsWith("Paper actions unavailable:")) ?? null;
  const accountCanBeUpdated = source === "api" && Boolean(snapshot.account?.portfolio_version);
  const accountReady = source === "demo" || Boolean(snapshot.account?.portfolio_version);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!planActionable || !snapshot.plan.market_as_of) return;
    const expiry = Date.parse(snapshot.plan.market_as_of) + PLAN_MARKET_MAX_AGE_MS;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, expiry - Date.now() + 1));
    return () => window.clearTimeout(timer);
  }, [planActionable, snapshot.plan.market_as_of]);

  useEffect(() => {
    const next = resolvePlanningSettings((savedSettings?.rules ?? DEFAULT_COMPETITION_PROFILE.rules) as CompetitionPlanningRules);
    setPlanStrategy(next.strategy);
    setUniverseMode(next.universe_mode);
    setTargetPositionValue(next.target_position_value);
    setMinimumPriceMode(next.minimum_price_mode);
    setMinimumPrice(String(next.minimum_price));
    setStopLossEnabled(next.stop_loss_enabled);
    setTakeProfitEnabled(next.take_profit_enabled);
  }, [savedSettings?.id, savedSettings?.updated_at]);

  useEffect(() => {
    setExitPreview(null); setExitPreviewError(null);
    if (!selected || selected.action !== "BUY" || (!stopLossEnabled && !takeProfitEnabled) || source !== "api" || !planActionable) return;
    const equity = snapshot.portfolio.value;
    if (equity === null || equity <= 0) {
      setExitPreviewError("Current account value is unavailable, so exit levels cannot be calculated.");
      return;
    }
    let active = true;
    void onStopPreview({ ticker: selected.ticker, available_cash: snapshot.portfolio.cash, account_equity: equity })
      .then((result) => { if (active) setExitPreview(result); })
      .catch((error) => { if (active) setExitPreviewError(error instanceof Error ? error.message : "Exit guidance is unavailable."); });
    return () => { active = false; };
  }, [selected, stopLossEnabled, takeProfitEnabled, source, snapshot.plan.id, planActionable, snapshot.portfolio.cash, snapshot.portfolio.value, onStopPreview]);

  async function saveSettings() {
    if (savingSettings || source !== "api") return;
    setSettingsSaveError(null); setSettingsSaved(false);
    const parsedMinimum = Number(minimumPrice);
    const rules: CompetitionPlanningRules = {
      strategy: planStrategy, target_position_value: targetPositionValue,
      universe_mode: universeMode,
      minimum_price_mode: minimumPriceMode,
      minimum_price: minimumPriceMode === "ANY" ? 5 : minimumPriceMode === "NO_MINIMUM" ? 0 : parsedMinimum,
      stop_loss_enabled: stopLossEnabled, take_profit_enabled: takeProfitEnabled,
    };
    try {
      effectiveMinimumPrice(rules);
      setSavingSettings(true);
      await onSaveSettings(rules);
      setSettingsSaved(true);
    } catch (error) {
      setSettingsSaveError(error instanceof Error ? error.message : "Settings could not be saved.");
    } finally { setSavingSettings(false); }
  }

  async function generatePlan() {
    if (buildingPlan || source !== "api") return;
    setBuildingPlan(true);
    setPlanError(null);
    try {
      await onBuildPlan(planStrategy, targetPositionValue, effectiveMinimumPrice({ minimum_price_mode: minimumPriceMode, minimum_price: Number(minimumPrice), strategy: planStrategy, target_position_value: targetPositionValue }), universeMode);
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : "Today’s plan could not be generated.");
    } finally {
      setBuildingPlan(false);
    }
  }
  async function downloadAccount() {
    if (exportBusy || source !== "api") return;
    setExportBusy(true);
    setExportError(null);
    try {
      await onExportAccount();
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Account export failed.");
    } finally {
      setExportBusy(false);
    }
  }
  function reviewPaperAction(item: Recommendation) {
    if (!isPlanActionable(snapshot.plan, Date.now())) {
      setRecommendationError("The plan market date is no longer current. Build a fresh plan before reviewing a paper action.");
      return;
    }
    const heldLong = snapshot.positions.find((position) => position.ticker === item.ticker && position.side === "LONG");
    setPaperQuantity(item.action === "BUY" ? proposedShares(item) ?? 0 : item.action === "SELL" ? Math.floor(heldLong?.quantity ?? 0) : 0);
    setPaperActionKey(crypto.randomUUID());
    setPaperActionError(null);
    setPaperAction(item);
  }
  async function confirmPaperAction() {
    if (!paperAction?.id || !paperActionKey || paperActionBusy || !snapshot.account) return;
    if (paperActionBlocker) {
      setPaperActionError(paperActionBlocker);
      return;
    }
    if (!isPlanActionable(snapshot.plan, Date.now())) {
      setPaperActionError("The plan market date is no longer current. Build a fresh plan before recording a paper action.");
      return;
    }
    if (paperAction.action === "BUY" && paperAction.price !== null && paperAction.price < 5) {
      setPaperActionError("Below-$5 research candidates cannot be recorded as competition paper BUY actions.");
      return;
    }
    setPaperActionBusy(true);
    setPaperActionError(null);
    try {
      await onRecordPaperAction(paperAction.id, paperQuantity, snapshot.account.portfolio_version, paperActionKey);
      setPaperAction(null);
    } catch (error) {
      setPaperActionError(error instanceof Error ? error.message : "Paper action could not be confirmed.");
    } finally {
      setPaperActionBusy(false);
    }
  }
  async function changeStatus(item: Recommendation, eventType: "DISMISSED" | "REOPENED") {
    if (!isPlanActionable(snapshot.plan, Date.now())) {
      setRecommendationError("The plan market date is no longer current. Build a fresh plan before changing recommendations.");
      return;
    }
    if (source === "demo") {
      setDismissed((current) => eventType === "DISMISSED"
        ? [...new Set([...current, item.ticker])]
        : current.filter((ticker) => ticker !== item.ticker));
      return;
    }
    if (!item.id || recommendationBusy) {
      setRecommendationError("This recommendation has no saved identity yet. Refresh the plan before changing it.");
      return;
    }
    setRecommendationBusy(true);
    setRecommendationError(null);
    try {
      await onRecommendationEvent(item.id, eventType);
    } catch (error) {
      setRecommendationError(error instanceof Error ? error.message : "Recommendation could not be updated.");
    } finally {
      setRecommendationBusy(false);
    }
  }
  useEffect(() => { setDismissed([]); setShowDismissed(false); }, [snapshot.plan.id]);
  const visibleRecommendations = useMemo(
    () => planActionable
      ? snapshot.recommendations.filter((item) => item.status !== "DISMISSED" && item.status !== "RECORDED" && !dismissed.includes(item.ticker))
      : [],
    [dismissed, planActionable, snapshot.recommendations],
  );
  const dismissedRecommendations = planActionable
    ? snapshot.recommendations.filter((item) => item.status === "DISMISSED" || dismissed.includes(item.ticker))
    : [];
  const invested = snapshot.portfolio.long_exposure === null || snapshot.portfolio.short_exposure === null
    ? null
    : snapshot.portfolio.long_exposure + Math.abs(snapshot.portfolio.short_exposure);
  const allocation = snapshot.portfolio.value && invested !== null ? (invested / snapshot.portfolio.value) * 100 : null;
  const generatedTime = snapshot.plan.generated_at
    ? new Date(snapshot.plan.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : "Unavailable";
  const marketAsOf = marketSessionDate(snapshot.plan.market_as_of);
  const snapshotDate = new Date(snapshot.snapshot_time);
  const notifications = useMemo(() => {
    const items = [...snapshot.plan.warnings];
    if (!snapshot.plan.generated_at) items.push("No current daily plan has been generated.");
    if (snapshot.plan.status !== "CURRENT") items.push(`Plan status is ${snapshot.plan.status}. Review freshness before acting.`);
    if (planDateWarning) items.push(planDateWarning);
    const missingPrices = snapshot.positions.filter((position) => position.price === null).map((position) => position.ticker);
    if (missingPrices.length) items.push(`Current prices are unavailable for: ${missingPrices.join(", ")}.`);
    return items;
  }, [snapshot, planDateWarning]);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="sidebar-top">
          <Logo />
          <button className="icon-button close-menu" onClick={() => setMenuOpen(false)} aria-label="Close menu"><X size={20} /></button>
        </div>
        <nav aria-label="Primary navigation">
          {nav.map(({ label, icon: Icon }) => (
            <button key={label} className={active === label ? "active" : ""} onClick={() => { setActive(label); setMenuOpen(false); }}>
              <Icon size={18} /> {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <LegalLinks />
          <button className={active === "Settings" ? "active" : ""} onClick={() => { setActive("Settings"); setMenuOpen(false); }}><Settings size={18} /> Settings</button>
          <div className="user-card">
            <span className="avatar">EC</span>
            <span><strong>{source === "demo" ? "Demo account" : "Competition account"}</strong><small>Paper trading</small></span>
            <button className="icon-button" onClick={onSignOut} aria-label="Sign out"><LogOut size={17} /></button>
          </div>
        </div>
      </aside>

      {menuOpen && <button className="menu-backdrop" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}

      <main className="main-content">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMenuOpen(true)} aria-label="Open menu"><Menu size={21} /></button>
          <div>
            <span className="date-line">{snapshotDate.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</span>
            <h1>{active}</h1>
          </div>
          <div className="topbar-actions">
            <span className="market-status"><i className="status-dot" /> Snapshot ready</span>
            <button className="icon-button" aria-label={`Notifications (${notifications.length})`} onClick={() => setNotificationsOpen(true)}>
              <Bell size={19} />{notifications.length > 0 && <i className="notification-dot" />}
            </button>
          </div>
        </header>

        <div className="content-wrap">
          <section className="notice prototype-notice">
            <Sparkles size={18} />
            <div><strong>{source === "demo" ? "Interactive demo" : "Cloud account"}</strong><span>{source === "demo" ? "Sample data only. Your local portfolio is never read." : `Structured API snapshot from ${new Date(snapshot.snapshot_time).toLocaleString()}.`}</span></div>
          </section>

          {active === "Overview" && <>
          {!hasPositions && !hasPlan && (
            <section className="getting-started-card" aria-label="Getting started">
              <div className="getting-started-copy">
                <span className="section-kicker">Start here</span>
                <h2>{accountReady ? "Your account is ready. Build your first daily plan." : "Set up your account, then build your first daily plan."}</h2>
                <p>{accountReady ? "Choose an existing strategy to scan the market. Nothing executes automatically." : "Add your real competition cash and holdings. They are saved as an account snapshot—not as fake trades."}</p>
                <button className="primary-button" onClick={() => accountReady ? setActive("Today's plan") : setEditingAccount(true)} disabled={source !== "api"}>
                  {accountReady ? <Compass size={17} /> : <WalletCards size={17} />} {accountReady ? "Build today’s plan" : "Add cash & holdings"}
                </button>
              </div>
              <ol className="setup-steps">
                <li className={accountReady ? "complete" : "current"}><span>{accountReady ? <Check size={16} /> : "1"}</span><div><strong>Add your account</strong><small>Cash, holdings, and cost basis</small></div></li>
                <li className={accountReady ? "current" : ""}><span>2</span><div><strong>Build today’s plan</strong><small>Scan the market with an existing strategy</small></div></li>
                <li><span>3</span><div><strong>Review recommendations</strong><small>Nothing executes automatically</small></div></li>
              </ol>
            </section>
          )}
          <section className="metrics-grid" aria-label="Account summary">
            <MetricCard label="Portfolio value" value={money(snapshot.portfolio.value)} detail="Current account snapshot" />
            <MetricCard label="Available cash" value={money(snapshot.portfolio.cash)} detail={allocation === null ? "Allocation unavailable" : `${(100 - allocation).toFixed(1)}% unallocated`} />
            <MetricCard label="Open exposure" value={money(invested)} detail={`${snapshot.portfolio.value && snapshot.portfolio.value > 0 && snapshot.portfolio.long_exposure !== null ? ((snapshot.portfolio.long_exposure / snapshot.portfolio.value) * 100).toFixed(1) : "—"}% long · ${snapshot.portfolio.value && snapshot.portfolio.value > 0 && snapshot.portfolio.short_exposure !== null ? ((Math.abs(snapshot.portfolio.short_exposure) / snapshot.portfolio.value) * 100).toFixed(1) : "—"}% short`} />
            <MetricCard label="Unrealized P&L" value={money(snapshot.portfolio.unrealized_pnl, true)} detail="On positions with current prices" trend={snapshot.portfolio.unrealized_pnl === null ? undefined : snapshot.portfolio.unrealized_pnl >= 0 ? "up" : "down"} />
          </section>

          <section className="plan-banner">
            <div className="plan-banner-main">
              <span className="section-kicker">Today’s plan</span>
              <h2>{hasPlan ? snapshot.plan.summary : "Your daily plan isn’t ready yet."}</h2>
              <p>{hasPlan ? `${snapshot.plan.strategy} · market data as of ${marketAsOf}.` : accountReady ? "Your account snapshot is ready. Build a recommendation-only plan to scan the market." : "Add your account details before building a plan."}</p>
            </div>
            <div className="plan-meta">
              <span><Clock3 size={15} /> Generated {generatedTime}</span>
              <span><Check size={15} /> {planActionable ? "Portfolio matched" : planStatus}</span>
              <button className="secondary-button" onClick={() => setActive("Today's plan")}>{hasPlan ? "Review full plan" : "See plan status"} <ChevronRight size={16} /></button>
            </div>
          </section>
          {planDateWarning && <section className="notice warning-notice" role="alert"><OctagonAlert size={18} /><div><strong>Plan needs a fresh market date</strong><p>{planDateWarning}</p></div></section>}
          {paperActionBlocker && <section className="notice warning-notice" role="alert"><OctagonAlert size={18} /><div><strong>Paper actions unavailable</strong><p>{paperActionBlocker}</p></div></section>}

          <div className="dashboard-grid">
            <section className="panel recommendations-panel">
              <div className="panel-heading">
                <div><span className="section-kicker">Ranked opportunities</span><h2>Current recommendations</h2></div>
                <button className="text-button" disabled={dismissedRecommendations.length === 0} onClick={() => source === "demo" ? setDismissed([]) : setShowDismissed((value) => !value)}>{source === "demo" ? "Restore dismissed" : showDismissed ? "Hide dismissed" : `Review dismissed (${dismissedRecommendations.length})`}</button>
              </div>
              {visibleRecommendations.length ? <div className="recommendation-list">
                {visibleRecommendations.map((item, index) => (
                  <article className="recommendation-row" key={item.ticker}>
                    <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                    <div className="ticker-cell"><strong>{item.ticker}</strong><span>{proposedShares(item) === null ? item.company : `${proposedShares(item)} whole shares · ${money(proposedAllocation(item))}`}</span></div>
                    <span className={`action-badge ${item.action.toLowerCase()}`}>{item.action}</span>
                    <div className="score-cell"><strong>{item.score.toFixed(1)}</strong><span>score</span></div>
                    <div className="price-cell"><strong>{item.price !== null ? `$${item.price.toFixed(2)}` : "Price unavailable"}</strong><span className={item.move === null ? "" : item.move >= 0 ? "positive" : "negative"}>{item.move === null ? "Move unavailable" : `${item.move >= 0 ? "+" : ""}${item.move.toFixed(1)}%`}</span></div>
                    <button className="row-button" onClick={() => setSelected(item)}>Inspect <ChevronRight size={16} /></button>
                    <button className="dismiss-button" disabled={recommendationBusy} onClick={() => void changeStatus(item, "DISMISSED")} aria-label={`Dismiss ${item.ticker}`}><X size={15} /></button>
                  </article>
                ))}
              </div> : <div className="empty-state compact"><Compass size={26} /><h3>No recommendations yet</h3><p>Recommendations will appear here after a current daily plan is available.</p></div>}
              {recommendationError && <p className="auth-error" role="alert">{recommendationError}</p>}
              {showDismissed && dismissedRecommendations.length > 0 && <div className="dismissed-list"><h3>Dismissed from this plan</h3>{dismissedRecommendations.map((item) => <div key={item.id ?? item.ticker}><span>{item.ticker} · {item.action}</span><button className="text-button" disabled={recommendationBusy} onClick={() => void changeStatus(item, "REOPENED")}>Restore</button></div>)}</div>}
            </section>

            <aside className="panel regime-panel">
              <div className="regime-orbit" aria-hidden="true"><Activity size={28} /></div>
              <span className="section-kicker">Market regime at plan generation</span>
              <h2>{snapshot.regime.label}</h2>
              <p>{snapshot.regime.reason}</p>
              <div className="regime-bars">
                <div><span>Trend strength</span><strong>{scoreMetric(snapshot.regime.trend_strength).label}</strong><i><b style={{ width: scoreMetric(snapshot.regime.trend_strength).width }} /></i></div>
                <div><span>Market breadth</span><strong>{scoreMetric(snapshot.regime.market_breadth).label}</strong><i><b style={{ width: scoreMetric(snapshot.regime.market_breadth).width }} /></i></div>
                <div><span>Volatility</span><strong>{scoreMetric(snapshot.regime.volatility).label}</strong><i><b style={{ width: scoreMetric(snapshot.regime.volatility).width }} /></i></div>
              </div>
              <div className="strategy-note">
                <span>Preferred today</span>
                {snapshot.regime.preferred_strategies.map((strategy) => <strong key={strategy}>{strategy}</strong>)}
              </div>
            </aside>
          </div>

          <section className="panel positions-panel">
            <div className="panel-heading">
              <div><span className="section-kicker">Capital in use</span><h2>Open positions</h2></div>
              <button className="text-button" onClick={() => setActive("Portfolio")}>View portfolio <ArrowRight size={16} /></button>
            </div>
            {hasPositions ? <div className="table-wrap">
              <table>
                <thead><tr><th>Position</th><th>Side</th><th>Quantity</th><th>Last price</th><th>Market value</th><th>Unrealized P&amp;L</th><th>Weight</th></tr></thead>
                <tbody>
                  {snapshot.positions.map((position) => (
                    <tr key={position.ticker}>
                      <td><strong>{position.ticker}</strong></td><td><span className="side-badge">{position.side}</span></td><td>{position.quantity}</td><td>{money(position.price)}</td><td>{money(position.value)}</td><td className={position.pnl !== null && position.pnl >= 0 ? "positive" : "negative"}>{money(position.pnl, true)}</td><td>{position.weight === null ? "—" : `${position.weight.toFixed(1)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div> : <div className="empty-state compact"><BriefcaseBusiness size={26} /><h3>No holdings added</h3><p>Add your current cash and positions to see exposure and capacity.</p>{source === "api" && <button className="secondary-button" onClick={() => setEditingAccount(true)}>Add holdings</button>}</div>}
          </section>
          </>}

          {active === "Today's plan" && (
            <section className="view-stack" aria-label="Today's plan details">
              {source === "api" && <section className="panel plan-builder">
                <div><span className="section-kicker">Recommendation only</span><h2>{hasPlan ? "Rebuild today’s plan" : "Build your first daily plan"}</h2><p>Consensus checks every live-enabled strategy; individual strategies remain selectable. Set a per-stock target to spread capital across more names when enough qualify. Shares use the last dated close, not a live fill. Nothing trades automatically.</p></div>
                <div className="plan-builder-controls"><div className="plan-settings-summary"><strong>{planStrategy}</strong><span>{universeMode === "BROAD" ? "Broad cross-sector" : "Expanded"} watchlist · {money(targetPositionValue)} per stock · {minimumPriceMode === "ANY" ? "Any eligible price ($5+)" : `Minimum $${minimumPrice}`} · {stopLossEnabled ? "Stop guide on" : "Stop guide off"}</span><button className="text-button" type="button" onClick={() => setActive("Settings")}>Adjust settings</button></div><button className="primary-button" disabled={buildingPlan || !accountReady || strategies.length === 0} onClick={() => void generatePlan()}>{buildingPlan ? "Scanning market…" : hasPlan ? "Rebuild plan" : "Build plan"}</button></div>
                {universeMode === "BROAD" && <p role="note">Broad mode uses a larger watchlist. A cold scan may take a minute or longer; if it reaches the server’s safety limit, no new plan is saved. Refresh account state before retrying after a timeout.</p>}
                {buildingPlan && <div className="plan-progress" role="status"><i /><span>Scanning the {universeMode === "BROAD" ? "broad" : "expanded"} watchlist: loading dated prices, detecting the regime, and ranking candidates. This request does not stream per-symbol progress; keep this page open.</span></div>}
                {planError && <p className="auth-error" role="alert">{planError}</p>}
              </section>}
              <section className="plan-banner full-plan-banner">
                <div className="plan-banner-main">
                  <span className="section-kicker">{planStatus}</span>
                  <h2>{snapshot.plan.generated_at ? snapshot.plan.summary : "No daily plan is available yet."}</h2>
                  <p>{snapshot.plan.generated_at ? `${snapshot.plan.strategy} · market data as of ${marketAsOf}.` : "Build a recommendation-only plan above. No trade executes automatically."}</p>
                </div>
                <div className="plan-meta">
                  <span><Clock3 size={15} /> Generated {generatedTime}</span>
                  <span><ShieldCheck size={15} /> Read-only recommendations</span>
                </div>
              </section>
              {snapshot.plan.warnings.length > 0 && (
                <section className="notice warning-notice"><OctagonAlert size={18} /><div><strong>Plan warnings and scan audit ({snapshot.plan.warnings.length})</strong><details><summary>Review dated-price and coverage details</summary><ul>{snapshot.plan.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul></details></div></section>
              )}
              <section className="panel recommendations-panel">
                <div className="panel-heading"><div><span className="section-kicker">Full plan</span><h2>Ranked recommendations</h2></div></div>
                {visibleRecommendations.length ? <div className="recommendation-list">
                  {visibleRecommendations.map((item, index) => (
                    <article className="recommendation-row" key={item.ticker}>
                      <span className="rank">{String(index + 1).padStart(2, "0")}</span>
                      <div className="ticker-cell"><strong>{item.ticker}</strong><span>{proposedShares(item) === null ? item.company : `${proposedShares(item)} whole shares · ${money(proposedAllocation(item))}`}</span></div>
                      <span className={`action-badge ${item.action.toLowerCase()}`}>{item.action}</span>
                      <div className="score-cell"><strong>{item.score.toFixed(1)}</strong><span>score</span></div>
                      <div className="price-cell"><strong>{item.price === null ? "Unavailable" : `$${item.price.toFixed(2)}`}</strong><span>{item.strategy}</span></div>
                      <button className="row-button" onClick={() => setSelected(item)}>Inspect <ChevronRight size={16} /></button>
                      <button className="dismiss-button" disabled={recommendationBusy} onClick={() => void changeStatus(item, "DISMISSED")} aria-label={`Dismiss ${item.ticker}`}><X size={15} /></button>
                    </article>
                  ))}
                </div> : <div className="empty-state"><Compass size={28} /><h3>No visible recommendations</h3><p>{planDateWarning ?? (dismissedRecommendations.length ? "Review dismissed recommendations below to restore one." : "Build a recommendation-only plan above to rank the current candidates.")}</p></div>}
                {recommendationError && <p className="auth-error" role="alert">{recommendationError}</p>}
                {dismissedRecommendations.length > 0 && <div className="dismissed-list"><h3>Dismissed from this plan</h3>{dismissedRecommendations.map((item) => <div key={item.id ?? item.ticker}><span>{item.ticker} · {item.action}</span><button className="text-button" disabled={recommendationBusy} onClick={() => void changeStatus(item, "REOPENED")}>Restore</button></div>)}</div>}
              </section>
            </section>
          )}

          {active === "Portfolio" && (
            <section className="view-stack" aria-label="Portfolio details">
              <section className="metrics-grid" aria-label="Portfolio summary">
                <MetricCard label="Portfolio value" value={money(snapshot.portfolio.value)} detail="Current account snapshot" />
                <MetricCard label="Cash" value={money(snapshot.portfolio.cash)} detail={allocation === null ? "Capacity unavailable" : `${(100 - allocation).toFixed(1)}% unallocated`} />
                <MetricCard label="Long exposure" value={money(snapshot.portfolio.long_exposure)} detail="Open long market value" />
                <MetricCard label="Short exposure" value={money(snapshot.portfolio.short_exposure)} detail="Open short market value" />
              </section>
              <section className="panel positions-panel portfolio-detail-panel">
                <div className="panel-heading"><div><span className="section-kicker">Account snapshot</span><h2>Open positions</h2></div><div className="panel-heading-actions"><span className="muted-label">{snapshot.positions.length} position{snapshot.positions.length === 1 ? "" : "s"}</span>{source === "api" && <button className="secondary-button compact-button" onClick={() => setEditingAccount(true)}>Edit account</button>}</div></div>
                {snapshot.positions.length ? <div className="table-wrap"><table><thead><tr><th>Position</th><th>Side</th><th>Quantity</th><th>Last price</th><th>Market value</th><th>Unrealized P&amp;L</th><th>Weight</th></tr></thead><tbody>
                  {snapshot.positions.map((position) => <tr key={`${position.ticker}-${position.side}`}><td><strong>{position.ticker}</strong></td><td><span className="side-badge">{position.side}</span></td><td>{position.quantity}</td><td>{money(position.price)}</td><td>{money(position.value)}</td><td className={position.pnl !== null && position.pnl >= 0 ? "positive" : position.pnl === null ? "" : "negative"}>{money(position.pnl, true)}</td><td>{position.weight === null ? "—" : `${position.weight.toFixed(1)}%`}</td></tr>)}
                </tbody></table></div> : <div className="empty-state"><BriefcaseBusiness size={28} /><h3>Your account has no holdings yet</h3><p>Add the positions you already own. They will be recorded as a snapshot, not as trades.</p>{source === "api" && <button className="primary-button" onClick={() => setEditingAccount(true)}>Add holdings</button>}</div>}
              </section>
            </section>
          )}

          {active === "History" && (
            <section className="view-stack" aria-label="Plan history">
              <section className="panel standalone-panel"><span className="section-kicker">Saved plans</span><h2>How your recommendations changed</h2><p>These are saved plan summaries. This view does not claim to show a complete paper-action history.</p>
                {historyError && <p className="auth-error" role="alert">{historyError}</p>}
                {history.length === 0 && !historyError && <p>No saved plans are available yet.</p>}
                {history.length >= 2 && (() => { const changes = compareSavedPlans(history[0], history[1]); return <div className="plan-delta"><h3>Latest plan versus previous plan</h3><p>Added: {changes.added.length ? changes.added.map((item) => item.ticker).join(", ") : "none"}</p><p>Dropped: {changes.dropped.length ? changes.dropped.map((item) => item.ticker).join(", ") : "none"}</p><p>Changed action or score: {changes.changed.length ? changes.changed.map((item) => `${item.ticker} (${item.prior.action} ${item.prior.score} → ${item.action} ${item.score})`).join("; ") : "none"}</p>{changes.dropped.map((item) => <details key={item.ticker}><summary>Why did {item.ticker} disappear?</summary><p>{item.explanation}</p></details>)}</div>; })()}
                <div className="plan-history-list">{history.map((item) => <article key={item.id}><div><strong>{new Date(item.generated_at).toLocaleString()}</strong><span>{item.status} · {item.strategy} · market data {marketSessionDate(item.market_as_of)}</span></div><p>{item.summary}</p><small>{item.recommendations.length} saved recommendation(s) · account version {item.portfolio_version}{item.cash_at_generation === null ? "" : ` · cash then ${money(item.cash_at_generation)}`}</small></article>)}</div>
              </section>
              <section className="panel standalone-panel"><span className="section-kicker">Paper account ledger</span><h2>Recent recorded actions</h2><p>These are simulated actions recorded from a saved daily-close estimate, not broker fills or real trades. This list shows up to 50 recent actions; use the account export for all stored rows.</p>{paperActionsError && <p className="auth-error" role="alert">{paperActionsError}</p>}{paperActionsAvailable && paperActions.length === 0 && <p>No paper actions have been recorded in this account.</p>}{paperActions.map((action) => <article className="paper-action-history" key={action.id}><strong>{action.action_type} {action.quantity} {action.ticker}</strong><span>{money(action.price)} estimated daily close · {new Date(action.occurred_at).toLocaleString()}</span>{action.action_type === "CLOSE" || action.action_type === "DECREASE" ? <small>Recorded realized P&amp;L: {money(action.realized_pnl)}</small> : null}</article>)}</section>
            </section>
          )}

          {active === "Research" && (
            <section className="view-stack">
              <section className="panel standalone-panel research-copy"><span className="section-kicker">Research only</span><h2>Current algorithm context</h2><p>Strategy and regime facts below came from the saved plan. VIX is a separate dated market reading; it does not alter these signals or place trades.</p><div className="research-grid"><div><span>Plan strategy</span><strong>{snapshot.plan.strategy || "Unavailable"}</strong></div><div><span>Regime at plan generation</span><strong>{snapshot.regime.label || "Unavailable"}</strong></div><div><span>Preferred strategies then</span><strong>{snapshot.regime.preferred_strategies.length ? snapshot.regime.preferred_strategies.join(", ") : "Unavailable"}</strong></div><div><span>Ranked candidates</span><strong>{snapshot.recommendations.length}</strong></div><div><span>VIX · implied volatility</span><strong>{vix?.value === null || !vix ? "Unavailable" : vix.value.toFixed(2)}{vix?.status === "STALE" ? " (stale)" : ""}</strong></div></div><p>VIX close dated {vix?.price_as_of ?? "unavailable"}. {vix?.warning ?? "This is not a live quote or a forecast for an individual stock."}</p></section>
              <section className="notice prototype-notice"><BookOpen size={18} /><div><strong>Benchmark results are intentionally separate</strong><span>This public prototype does not run historical benchmarks or expose research files.</span></div></section>
            </section>
          )}

          {active === "Settings" && (
            <section className="view-stack">
              <section className="panel standalone-panel settings-panel"><span className="section-kicker">Daily planning</span><h2>Recommendation settings</h2><p>These apply to the next plan you build. They never place orders. Saved settings are tied to your signed-in account; older profile records remain intact but are no longer shown as separate workspaces.</p>
                <div className="planning-settings-fields">
                  <label>Strategy<select value={planStrategy} disabled={savingSettings} onChange={(event) => { setPlanStrategy(event.target.value); setSettingsSaved(false); }}>{strategies.map((strategy) => <option key={strategy}>{strategy}</option>)}</select></label>
                  <label>Stock universe<select value={universeMode} disabled={savingSettings} onChange={(event) => { setUniverseMode(event.target.value as "EXPANDED" | "BROAD"); setSettingsSaved(false); }}><option value="EXPANDED">Expanded daily watchlist</option><option value="BROAD">Broad cross-sector watchlist</option></select></label>
                  <label>Target per stock<select value={targetPositionValue} disabled={savingSettings} onChange={(event) => { setTargetPositionValue(Number(event.target.value)); setSettingsSaved(false); }}>{[5000, 10000, 15000, 20000].map((amount) => <option value={amount} key={amount}>{money(amount)}</option>)}</select></label>
                  <label>Minimum stock price<select value={minimumPriceMode} disabled={savingSettings} onChange={(event) => { setMinimumPriceMode(event.target.value as "ANY" | "CUSTOM" | "NO_MINIMUM"); setSettingsSaved(false); }}><option value="ANY">Any eligible price (algorithm decides)</option><option value="NO_MINIMUM">No price minimum — research only</option><option value="CUSTOM">Custom minimum</option></select></label>
                  {minimumPriceMode === "CUSTOM" && <label>Custom minimum price (USD)<input type="number" min="0.01" max="1000000" step="0.01" value={minimumPrice} disabled={savingSettings} onChange={(event) => { setMinimumPrice(event.target.value); setSettingsSaved(false); }} /></label>}
                </div>
                <p><strong>Any eligible price</strong> keeps the $5 competition floor. No minimum or a custom floor under $5 creates a research-only plan: sub-$5 BUY ideas cannot be recorded as competition paper actions, and their proposed allocations are hypothetical.</p>
                <p>Broad mode searches more current-day symbols and can take longer. It does not change historical benchmark membership; a watchlist ticker still needs a valid dated quote and competition eligibility review.</p>
                <div className="planning-settings-toggles"><label><input type="checkbox" checked={stopLossEnabled} disabled={savingSettings} onChange={(event) => { setStopLossEnabled(event.target.checked); setSettingsSaved(false); }} /> Show automatic stop-loss SELL guidance</label><label><input type="checkbox" checked={takeProfitEnabled} disabled={savingSettings} onChange={(event) => { setTakeProfitEnabled(event.target.checked); setSettingsSaved(false); }} /> Show automatic take-profit SELL guidance</label></div>
                <p>Exit prices use dated daily data and appear when inspecting a current BUY recommendation. They are not live orders or automatic sales.</p>
                {settingsError && <p className="auth-error" role="alert">{settingsError}</p>}{settingsSaveError && <p className="auth-error" role="alert">{settingsSaveError}</p>}{settingsSaved && <p role="status">Settings saved. Rebuild the plan to apply changed price or universe settings.</p>}
                <div className="settings-actions"><button className="primary-button" type="button" disabled={savingSettings || source !== "api" || !settingsLoaded} onClick={() => void saveSettings()}>{source !== "api" ? "Sign in to save settings" : savingSettings ? "Saving…" : settingsLoaded ? "Save settings" : "Loading settings…"}</button></div>
              </section>
              <section className="panel standalone-panel settings-panel"><span className="section-kicker">Account</span><h2>{snapshot.account?.name ?? (source === "demo" ? "Demo account" : "Competition account")}</h2><p>{source === "demo" ? "This session uses sample data and does not access your local trading database." : "Update cash and holdings without creating fake trades. Every saved account snapshot remains in history."}</p>{source === "api" && <div className="settings-meta"><div><span>Snapshot date</span><strong>{snapshot.account?.snapshot_as_of ? new Date(snapshot.account.snapshot_as_of).toLocaleDateString() : "Unavailable"}</strong></div><div><span>Account status</span><strong>{accountCanBeUpdated ? "Ready to update" : "Cloud update required"}</strong></div></div>}<div className="settings-actions">{source === "api" && <button className="primary-button" onClick={() => setEditingAccount(true)}>Edit account &amp; holdings</button>}<button className="secondary-button" onClick={onSignOut}><LogOut size={16} /> {source === "demo" ? "Exit demo" : "Sign out"}</button></div></section>
              <section className="panel standalone-panel settings-panel"><span className="section-kicker">Privacy & legal</span><h2>Data controls</h2><p>Download all of your available application-table records as JSON. Export is read-only and does not include your identity-provider account or credentials. Concurrent edits may appear across different table pages; deletion is not yet self-service.</p>{source === "api" && <div className="settings-actions"><button className="secondary-button" disabled={exportBusy} onClick={() => void downloadAccount()}>{exportBusy ? "Preparing export…" : "Download my account data"}</button></div>}{exportError && <p className="auth-error" role="alert">{exportError}</p>}<LegalLinks /></section>
            </section>
          )}
        </div>
      </main>

      {selected && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setSelected(null)}>
          <section className="detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="icon-button modal-close" onClick={() => setSelected(null)} aria-label="Close details"><X size={20} /></button>
            <span className={`action-badge ${selected.action.toLowerCase()}`}>{selected.action}</span>
            <h2 id="detail-title">{selected.ticker} <span>{selected.company}</span></h2>
            <div className="modal-score"><strong>{selected.score.toFixed(1)}</strong><span>recommendation score</span></div>
            <h3>Why it appears</h3>
            <p>{selected.reason}</p>
            {planDateWarning && <p className="auth-error" role="alert">{planDateWarning}</p>}
            <div className="detail-facts">
              <span><small>Strategy</small><strong>{selected.strategy}</strong></span>
              <span><small>Algorithm signal</small><strong>{selected.signal ?? selected.action}</strong></span>
              <span><small>Last price</small><strong>{selected.price !== null ? `$${selected.price.toFixed(2)}` : "Unavailable"}</strong></span>
              <span><small>Price as of</small><strong>{selected.price_as_of ? new Date(selected.price_as_of).toLocaleString() : "Unavailable"}</strong></span>
              {proposedShares(selected) !== null && <span><small>Suggested size</small><strong>{proposedShares(selected)} whole shares · {money(proposedAllocation(selected))}</strong></span>}
            </div>
            {planActionable && selected.action === "BUY" && (stopLossEnabled || takeProfitEnabled) && <div className="exit-guidance" aria-label="Optional paper exit guidance"><h3>Optional paper exit guidance</h3>{exitPreview ? <><p>Calculated from daily OHLCV as of {exitPreview.price_as_of}; verify your actual entry and share count first.</p><div className="exit-guidance-levels">{stopLossEnabled && <div><span>Stop-loss SELL trigger</span><strong>{money(exitPreview.stop_watch_price)}</strong></div>}{takeProfitEnabled && <div><span>Take-profit SELL limit</span><strong>{money(exitPreview.take_profit_watch_price)}</strong></div>}</div><small>These are reference levels, not submitted orders. If using both in TradingView Paper Trading, attach them as paired exits—not two independent sells. A stop can fill below its trigger.</small></> : <p>{exitPreviewError ?? "Checking dated prices for exit guidance…"}</p>}</div>}
            {selected.factor_details && Object.keys(selected.factor_details).length > 0 && <div className="factor-details"><h3>Factors and allocation</h3><dl>{Object.entries(selected.factor_details).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{detailValue(value)}</dd></div>)}</dl></div>}
            <div className="risk-note"><OctagonAlert size={17} /><span>{selected.action === "BUY" && selected.price !== null && selected.price < 5 ? "Below-$5 research idea only; competition paper BUY is unavailable." : "This is an algorithm signal, not a trade. Review allocation and price freshness before recording a paper action."}</span></div>
            <div className="modal-actions">
              <button className="secondary-button" disabled={recommendationBusy || !planActionable} onClick={() => { void changeStatus(selected, "DISMISSED"); setSelected(null); }}>Dismiss</button>
              <button className="primary-button" disabled={!planActionable || Boolean(paperActionBlocker) || selected.action === "BUY" && selected.price !== null && selected.price < 5} onClick={() => { reviewPaperAction(selected); setSelected(null); }}>Review paper action <ArrowRight size={16} /></button>
            </div>
          </section>
        </div>
      )}

      {paperAction && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => { if (!paperActionBusy) setPaperAction(null); }}>
          <section className="detail-modal" role="dialog" aria-modal="true" aria-labelledby="paper-action-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="icon-button modal-close" disabled={paperActionBusy} onClick={() => setPaperAction(null)} aria-label="Close paper action review"><X size={20} /></button>
            <span className="section-kicker">Explicit paper action review</span><h2 id="paper-action-title">{paperAction.action} {paperAction.ticker}</h2>
            <p>{paperAction.reason}</p>
            {planDateWarning && <p className="auth-error" role="alert">{planDateWarning}</p>}
            {paperActionBlocker && <p className="auth-error" role="alert">{paperActionBlocker}</p>}
            <div className="detail-facts"><span><small>Signal</small><strong>{paperAction.action}</strong></span><span><small>Score</small><strong>{paperAction.score.toFixed(1)}</strong></span><span><small>Price</small><strong>{paperAction.price === null ? "Unavailable" : `$${paperAction.price.toFixed(2)}`}</strong></span></div>
            <p><strong>Estimated fill:</strong> the saved daily close shown above, dated {marketSessionDate(paperAction.price_as_of)}. This is not a live quote or a broker trade. A successful recording will change your paper cash and holdings and make this plan stale.</p>
            {(paperAction.action === "BUY" || paperAction.action === "SELL") && <label htmlFor="paper-action-quantity">Whole shares to record<input id="paper-action-quantity" type="number" min="1" step="1" value={paperQuantity} disabled={paperActionBusy} onChange={(event) => setPaperQuantity(Number(event.target.value))} /></label>}
            {paperAction.price !== null && Number.isInteger(paperQuantity) && paperQuantity > 0 && <p>Estimated value: <strong>{money(paperQuantity * paperAction.price)}</strong>. Fees and slippage are not included.</p>}
            <div className="risk-note"><ShieldCheck size={17} /><span>{paperActionsAvailable ? "The server will recheck the plan, quote date, cash, shared capacity, and per-stock limit in one transaction. This action is paper-only." : paperActionsError ?? "Checking whether paper-action storage is available. Nothing has been submitted."}</span></div>
            {paperActionError && <p className="auth-error" role="alert">{paperActionError}</p>}
            <div className="modal-actions"><button className="secondary-button" disabled={paperActionBusy} onClick={() => setPaperAction(null)}>Close</button><button className="primary-button" disabled={!paperActionsAvailable || paperActionBusy || source !== "api" || !planActionable || Boolean(paperActionBlocker) || paperAction.status === "DISMISSED" || paperAction.status === "RECORDED" || !paperAction.id || paperAction.price === null || (paperAction.action === "BUY" && paperAction.price < 5) || !Number.isInteger(paperQuantity) || paperQuantity <= 0 || !["BUY", "SELL"].includes(paperAction.action)} onClick={() => void confirmPaperAction()}>{paperActionBusy ? "Recording…" : paperActionsAvailable ? "Confirm paper action" : "Recording unavailable"}</button></div>
          </section>
        </div>
      )}

      {notificationsOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setNotificationsOpen(false)}>
          <section className="detail-modal notification-modal" role="dialog" aria-modal="true" aria-labelledby="notifications-title" onMouseDown={(event) => event.stopPropagation()}>
            <button className="icon-button modal-close" onClick={() => setNotificationsOpen(false)} aria-label="Close notifications"><X size={20} /></button>
            <span className="section-kicker">Snapshot checks</span><h2 id="notifications-title">Notifications</h2>
            {notifications.length ? <div className="notification-list">{notifications.map((item, index) => <div key={`${item}-${index}`}><OctagonAlert size={17} /><span>{item}</span></div>)}</div> : <div className="empty-state compact"><Check size={26} /><h3>No current warnings</h3><p>The loaded snapshot does not report stale plans, missing prices, or plan warnings.</p></div>}
          </section>
        </div>
      )}

      {editingAccount && (
        <div className="modal-backdrop account-editor-backdrop" role="presentation">
          <div className="account-editor-modal" role="dialog" aria-modal="true" aria-label="Update competition account">
            <AccountEditor snapshot={snapshot} onCancel={() => setEditingAccount(false)} onSave={async (account) => { await onUpdateAccount(account); setEditingAccount(false); }} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const [accessMode, setAccessMode] = useState<"signed-out" | "loading" | "demo" | "onboarding" | "authenticated">("signed-out");
  const [snapshot, setSnapshot] = useState<PublicSnapshot>(DEMO_SNAPSHOT);
  const [authError, setAuthError] = useState<string | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [oauthAvailability, setOAuthAvailability] = useState({ google: false, azure: false });
  const [strategies, setStrategies] = useState<string[]>([]);
  const [history, setHistory] = useState<PlanHistoryItem[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [paperActions, setPaperActions] = useState<PaperAction[]>([]);
  const [paperActionsAvailable, setPaperActionsAvailable] = useState(false);
  const [paperActionsError, setPaperActionsError] = useState<string | null>(null);
  const [planningProfiles, setPlanningProfiles] = useState<PlanningProfile[]>([]);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [vix, setVix] = useState<VixContext | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const sessionGeneration = useRef(0);
  const renderedGeneration = sessionGeneration.current;

  function currentToken(): string {
    if (!accessToken || renderedGeneration !== sessionGeneration.current) {
      throw new Error("Your session changed. Please wait for the new account to load.");
    }
    return accessToken;
  }

  function clearAccountState() {
    setSnapshot(DEMO_SNAPSHOT);
    setVix(null);
    setStrategies([]);
    setHistory([]);
    setHistoryError(null);
    setPaperActions([]);
    setPaperActionsAvailable(false);
    setPaperActionsError(null);
    setPlanningProfiles([]);
    setProfileError(null);
    setSettingsLoaded(false);
  }

  useEffect(() => {
    let active = true;
    void getOAuthAvailability().then((availability) => {
      if (active) setOAuthAvailability(availability);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    if (!accessToken) return;
    const generation = sessionGeneration.current;
    const stillCurrent = () => active && generation === sessionGeneration.current;
    void loadStrategies(accessToken).then((items) => { if (stillCurrent()) setStrategies(items); }).catch(() => { if (stillCurrent()) setStrategies([]); });
    void loadVixContext(accessToken).then((reading) => { if (stillCurrent()) setVix(reading); }).catch(() => { if (stillCurrent()) setVix(null); });
    void loadPlanHistory(accessToken).then((items) => { if (stillCurrent()) { setHistory(items); setHistoryError(null); } }).catch((error) => { if (stillCurrent()) { setHistory([]); setHistoryError(error instanceof Error ? error.message : "Plan history is unavailable."); } });
    void loadPaperActions(accessToken).then((items) => { if (stillCurrent()) { setPaperActions(items); setPaperActionsAvailable(true); setPaperActionsError(null); } }).catch((error) => { if (stillCurrent()) { setPaperActions([]); setPaperActionsAvailable(false); setPaperActionsError(error instanceof Error ? error.message : "Paper action storage is unavailable."); } });
    void loadPlanningProfiles(accessToken).then((items) => {
      if (!stillCurrent()) return;
      setPlanningProfiles(items);
      setProfileError(null);
      setSettingsLoaded(true);
    }).catch((error) => {
      if (stillCurrent()) { setPlanningProfiles([]); setSettingsLoaded(false); setProfileError(error instanceof Error ? error.message : "Settings are unavailable."); }
    });
    return () => { active = false; };
  }, [accessToken, renderedGeneration]);

  useEffect(() => {
    const client = getSupabaseClient();
    if (!client) return;
    let active = true;
    let requestVersion = 0;
    const initialGeneration = sessionGeneration.current;

    async function restoreAccount(token: string) {
      const version = ++requestVersion;
      const generation = ++sessionGeneration.current;
      clearAccountState();
      setAccessMode("loading");
      setAuthError(null);
      setAccessToken(token);
      try {
        const loaded = await loadSnapshot(token);
        if (!active || version !== requestVersion || generation !== sessionGeneration.current) return;
        setSnapshot(loaded);
        setAccessMode("authenticated");
        setAuthError(null);
      } catch (error) {
        if (!active || version !== requestVersion || generation !== sessionGeneration.current) return;
        if (error instanceof AccountNotFoundError) {
          setAccessMode("onboarding");
          setAuthError(null);
        } else {
          requestVersion++;
          sessionGeneration.current++;
          clearAccountState();
          setAccessToken(null);
          setAccessMode("signed-out");
          setAuthError(error instanceof Error ? error.message : "Account data is unavailable.");
        }
      }
    }

    void client.auth.getSession().then(({ data }) => {
      if (active && requestVersion === 0 && sessionGeneration.current === initialGeneration && data.session) void restoreAccount(data.session.access_token);
    }).catch(() => {
      if (active && requestVersion === 0 && sessionGeneration.current === initialGeneration) setAuthError("Your saved session could not be restored. Please sign in again.");
    });

    const { data: listener } = client.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_OUT") {
        requestVersion++;
        sessionGeneration.current++;
        setAccessToken(null);
        setAccessMode("signed-out");
        clearAccountState();
        setAuthError(null);
      } else if (session && event === "TOKEN_REFRESHED") {
        void restoreAccount(session.access_token);
      } else if (session && event === "SIGNED_IN") {
        void restoreAccount(session.access_token);
      }
    });
    return () => {
      active = false;
      requestVersion++;
      listener.subscription.unsubscribe();
    };
  }, []);

  async function openDemo() {
    const generation = ++sessionGeneration.current;
    setAccessToken(null);
    clearAccountState();
    setAccessMode("loading");
    setAuthError(null);
    try {
      const loaded = await loadSnapshot();
      if (generation !== sessionGeneration.current) return;
      setSnapshot(loaded);
      setAccessMode("demo");
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      setAccessMode("signed-out");
      setAuthError(error instanceof Error ? error.message : "Demo data is unavailable.");
    }
  }

  async function beginOAuth(provider: "google" | "azure") {
    setAuthError(null);
    try {
      await startOAuth(provider);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Sign in could not be started.");
    }
  }

  async function beginMagicLink(email: string) {
    setAuthError(null);
    await sendMagicLink(email);
  }

  async function signOut() {
    if (renderedGeneration !== sessionGeneration.current) return;
    sessionGeneration.current++;
    const shouldSignOut = accessMode === "authenticated" || accessMode === "onboarding";
    setAccessToken(null);
    setAccessMode("signed-out");
    clearAccountState();
    setAuthError(null);
    if (shouldSignOut) await getSupabaseClient()?.auth.signOut();
  }

  async function saveSettings(rules: CompetitionPlanningRules) {
    const token = currentToken();
    if (!settingsLoaded) throw new Error("Existing settings have not loaded; retry after reconnecting so they are not duplicated.");
    const generation = sessionGeneration.current;
    const existing = managedAppSettingsProfile(planningProfiles);
    const input: PlanningProfileInput = { name: "App settings", mode: "COMPETITION", rules: { ...rules, app_settings: true } };
    const saved = await savePlanningProfile(token, input, existing);
    if (generation !== sessionGeneration.current) return;
    setPlanningProfiles((current) => existing
      ? current.map((item) => item.id === saved.id ? saved : item)
      : [...current, saved]);
    setProfileError(null);
  }

  async function finishAccountSetup(account: { name: string; starting_capital: number; cash: number; snapshot_as_of: string }) {
    if (renderedGeneration !== sessionGeneration.current) return;
    if (!accessToken) {
      setAuthError("Your session expired. Please sign in again.");
      return;
    }
    setAuthError(null);
    const generation = sessionGeneration.current;
    try {
      const updated = await createAccount(accessToken, account);
      if (generation !== sessionGeneration.current) return;
      setSnapshot(updated);
      setAccessMode("authenticated");
    } catch (error) {
      if (generation === sessionGeneration.current) setAuthError(error instanceof Error ? error.message : "Your account could not be saved.");
    }
  }

  async function saveAccountUpdate(account: AccountUpdate) {
    const token = currentToken();
    const generation = sessionGeneration.current;
    const updated = await updateAccount(token, account);
    if (generation === sessionGeneration.current) setSnapshot(updated);
  }

  async function generateDailyPlan(strategy: string, targetPositionValue: number, minimumPrice: number, universeMode: "EXPANDED" | "BROAD") {
    const token = currentToken();
    const generation = sessionGeneration.current;
    const updated = await buildDailyPlan(token, strategy, targetPositionValue, minimumPrice, universeMode);
    if (generation !== sessionGeneration.current) return;
    setSnapshot(updated);
    void loadPlanHistory(token).then((items) => { if (generation === sessionGeneration.current) { setHistory(items); setHistoryError(null); } }).catch((error) => { if (generation === sessionGeneration.current) { setHistory([]); setHistoryError(error instanceof Error ? error.message : "Plan history is unavailable."); } });
  }

  async function saveRecommendationEvent(id: string, eventType: "DISMISSED" | "REOPENED") {
    const token = currentToken();
    const generation = sessionGeneration.current;
    const updated = await changeRecommendationStatus(token, id, eventType);
    if (generation !== sessionGeneration.current) return;
    setSnapshot(updated);
    void loadPlanHistory(token).then((items) => { if (generation === sessionGeneration.current) { setHistory(items); setHistoryError(null); } }).catch((error) => { if (generation === sessionGeneration.current) { setHistory([]); setHistoryError(error instanceof Error ? error.message : "Plan history is unavailable."); } });
  }

  async function savePaperAction(recommendationId: string, quantity: number, expectedVersion: number, idempotencyKey: string) {
    const token = currentToken();
    const generation = sessionGeneration.current;
    const result = await recordPaperAction(token, {
      recommendation_id: recommendationId, quantity,
      expected_portfolio_version: expectedVersion, idempotency_key: idempotencyKey,
    });
    if (generation !== sessionGeneration.current) return;
    setSnapshot(result.snapshot);
    void loadPlanHistory(token).then((items) => { if (generation === sessionGeneration.current) { setHistory(items); setHistoryError(null); } }).catch((error) => { if (generation === sessionGeneration.current) { setHistory([]); setHistoryError(error instanceof Error ? error.message : "Plan history is unavailable."); } });
    void loadPaperActions(token).then((items) => { if (generation === sessionGeneration.current) { setPaperActions(items); setPaperActionsAvailable(true); setPaperActionsError(null); } }).catch((error) => { if (generation === sessionGeneration.current) setPaperActionsError(error instanceof Error ? error.message : "Paper action was saved, but its history could not be refreshed."); });
  }

  async function exportAccount() {
    const token = currentToken();
    const generation = sessionGeneration.current;
    const archive = await loadAccountArchive(token);
    if (generation !== sessionGeneration.current) throw new Error("The session changed while exporting. Sign in and try again.");
    const blob = new Blob([JSON.stringify(archive, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `stock-comp-account-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  if (accessMode === "signed-out") return (
    <SignIn onDemo={() => void openDemo()} onOAuth={(provider) => void beginOAuth(provider)} onMagicLink={beginMagicLink} onGoogleError={setAuthError} googleReady={oauthAvailability.google} microsoftReady={oauthAvailability.azure} magicReady={supabaseConfigured} demoReady={demoMode} error={authError} />
  );
  if (accessMode === "loading") return <main className="auth-page" role="status">Loading account…</main>;
  if (accessMode === "onboarding") return <AccountSetup onSave={finishAccountSetup} onSignOut={() => void signOut()} error={authError} />;
  const savedSettings = savedAppSettingsProfile(planningProfiles) ?? null;
  return <AppShell
    onSignOut={() => void signOut()} onUpdateAccount={saveAccountUpdate}
    onBuildPlan={generateDailyPlan} onRecommendationEvent={saveRecommendationEvent} onExportAccount={exportAccount} onRecordPaperAction={savePaperAction}
    strategies={strategies} history={history} historyError={historyError}
    paperActions={paperActions} paperActionsAvailable={paperActionsAvailable} paperActionsError={paperActionsError}
    snapshot={snapshot} source={accessMode === "demo" ? "demo" : "api"} vix={vix}
    savedSettings={savedSettings} settingsLoaded={settingsLoaded} onSaveSettings={saveSettings} settingsError={profileError}
    onStopPreview={(input) => loadTradingViewAutoPlan(currentToken(), input)}
  />;
}
