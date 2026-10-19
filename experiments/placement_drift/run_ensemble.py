"""
Quick CLI Runner for Multi-Service Parametric Ensemble Evaluation.
Features a rich, clean terminal dashboard with color-coded drift indicators,
visual progress bars, and categorized trial ledgers.
"""
import os
import sys
from pathlib import Path

# Enable UTF-8 encoding on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from experiments.placement_drift.parametric_ensemble_evaluator import ParametricEnsembleEvaluator


# Terminal styling helpers
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"


def drift_color(d_rate: float) -> str:
    if d_rate >= 0.50:
        return RED
    elif d_rate >= 0.20:
        return YELLOW
    return GREEN


def make_bar(percent: float, length: int = 10) -> str:
    filled = int(round((percent / 100.0) * length))
    filled = max(0, min(length, filled))
    empty = length - filled
    return "█" * filled + "░" * empty


def render_cli_dashboard(metrics) -> None:
    width = 96
    border = "═" * (width - 2)

    # 1. Main Header
    print(f"\n{CYAN}{BOLD}╔{border}╗{RESET}")
    title = "K8s Resilience Engine · Multi-Service Chaos Ensemble (N = 28)"
    subtitle = "Google Boutique Microservice Placement Drift & Failure-Domain Sensitivity"
    print(f"{CYAN}{BOLD}║{RESET}  {WHITE}{BOLD}{title.center(width - 6)}{RESET}  {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}║{RESET}  {DIM}{subtitle.center(width - 6)}{RESET}  {CYAN}{BOLD}║{RESET}")
    print(f"{CYAN}{BOLD}╚{border}╝{RESET}\n")

    # 2. Executive KPI Summary Cards
    print(f"{BOLD}{WHITE}1. STATISTICAL EXECUTIVE SUMMARY{RESET}")
    print(f"{DIM}{'─' * width}{RESET}")

    card_w = 21
    card_border = "─" * card_w
    print(f"  ┌{card_border}┬{card_border}┬{card_border}┬{card_border}┐")
    print(f"  │ {'TOTAL TRIALS':<{card_w}}│ {'MEAN DRIFT RATE (μ)':<{card_w}}│ {'95% CONF. INTERVAL':<{card_w}}│ {'FAILURE EXPOSURE (Δ)':<{card_w}}│")
    
    mean_pct = metrics.mean_d_rate * 100
    ci_low = metrics.confidence_interval_95[0] * 100
    ci_high = metrics.confidence_interval_95[1] * 100
    delta_e_pct = metrics.mean_delta_e_rate * 100

    t1 = f"{metrics.total_trials} Empirical Trials"
    t2 = f"{mean_pct:.1f}% (σ = {metrics.std_dev_d_rate*100:.1f}%)"
    t3 = f"[{ci_low:.1f}%, {ci_high:.1f}%]"
    t4 = f"+{delta_e_pct:.1f}% Increased"

    print(f"  │ {BOLD}{t1:<{card_w}}{RESET}│ {YELLOW}{BOLD}{t2:<{card_w}}{RESET}│ {CYAN}{BOLD}{t3:<{card_w}}{RESET}│ {RED}{BOLD}{t4:<{card_w}}{RESET}│")
    print(f"  └{card_border}┴{card_border}┴{card_border}┴{card_border}┘")
    print()

    # 3. Microservice Sensitivity Ranking
    print(f"{BOLD}{WHITE}2. MICROSERVICE DRIFT SENSITIVITY BREAKDOWN{RESET}")
    print(f"{DIM}{'─' * width}{RESET}")
    print(f"  {DIM}{'Microservice':<24} {'Trials':<8} {'Mean Drift':<12} {'Visual Distribution':<16} {'Resilience Tier'}{RESET}")
    print(f"  {DIM}{'─' * 24} {'─' * 8} {'─' * 12} {'─' * 16} {'─' * 24}{RESET}")

    TIER_CRITICAL_DRIFT = "CRITICAL DRIFT"
    TIER_MODERATE_DRIFT = "MODERATE DRIFT"
    TIER_MINIMAL_DRIFT = "MINIMAL DRIFT"

    tier_map = {
        "multi_workload": (TIER_CRITICAL_DRIFT, RED),
        "productcatalogservice": (TIER_MODERATE_DRIFT, YELLOW),
        "paymentservice": ("MODERATE DRIFT (SPOF)", RED),
        "cartservice": (TIER_MODERATE_DRIFT, YELLOW),
        "checkoutservice": (TIER_MINIMAL_DRIFT, GREEN),
        "shippingservice": (TIER_MINIMAL_DRIFT, GREEN),
        "frontend": (TIER_MINIMAL_DRIFT, GREEN),
        "all_services": ("CONTROL BASELINE", BLUE),
    }

    for svc, rate in sorted(metrics.per_service_drift.items(), key=lambda x: x[1], reverse=True):
        tier_label, tier_col = tier_map.get(svc, ("STABLE", GREEN))
        count = sum(1 for t in metrics.trials if t.target_service == svc)
        rate_pct = rate * 100
        bar = make_bar(rate_pct, length=12)
        c = drift_color(rate)
        print(f"  {WHITE}{BOLD}{svc:<24}{RESET} {count:<8} {c}{BOLD}{rate_pct:>5.1f}%{RESET}     {c}{bar}{RESET}    {tier_col}{tier_label}{RESET}")
    print()

    # 4. Disruption Categories Sensitivity
    print(f"{BOLD}{WHITE}3. DISRUPTION SCENARIO CATEGORY SENSITIVITY{RESET}")
    print(f"{DIM}{'─' * width}{RESET}")
    print(f"  {DIM}{'Scenario Category':<24} {'Trials':<8} {'Mean Drift':<12} {'Visual Impact':<16} {'Observed Cluster Impact'}{RESET}")
    print(f"  {DIM}{'─' * 24} {'─' * 8} {'─' * 12} {'─' * 16} {'─' * 28}{RESET}")

    cat_desc = {
        "NODE_DRAIN": ("HIGH DISPLACEMENT", RED),
        "SCALE_DOWN_LOSS": ("BALANCED RE-PLACEMENT", YELLOW),
        "POD_DELETION": ("DYNAMIC RESCHEDULE", YELLOW),
        "ROLLING_UPDATE": ("DEPLOYMENT CHURN", YELLOW),
        "SCALE_UP_STRESS": ("STABLE DISTRIBUTION", GREEN),
        "BASELINE": ("CONTROL OBSERVATION", BLUE),
    }

    for cat, rate in sorted(metrics.per_disruption_drift.items(), key=lambda x: x[1], reverse=True):
        desc, col = cat_desc.get(cat, ("STABLE", GREEN))
        count = sum(1 for t in metrics.trials if t.scenario_category == cat)
        rate_pct = rate * 100
        bar = make_bar(rate_pct, length=12)
        c = drift_color(rate)
        print(f"  {WHITE}{BOLD}{cat:<24}{RESET} {count:<8} {c}{BOLD}{rate_pct:>5.1f}%{RESET}     {c}{bar}{RESET}    {col}{desc}{RESET}")
    print()

    # 5. Categorized Trial Ledger
    print(f"{BOLD}{WHITE}4. EMPIRICAL PARAMETRIC TRIAL LEDGER (N = 28){RESET}")
    print(f"{DIM}{'─' * width}{RESET}")
    
    categories = [
        ("CONTROL BASELINE", "BASELINE", BLUE),
        ("SCALE-UP STRESS SCENARIOS", "SCALE_UP_STRESS", GREEN),
        ("SCALE-DOWN LOSS & SPOF BOUNDARIES", "SCALE_DOWN_LOSS", YELLOW),
        ("ROLLING UPDATE CHURN SCENARIOS", "ROLLING_UPDATE", MAGENTA),
        ("POD DELETION CHAOS SCENARIOS", "POD_DELETION", YELLOW),
        ("INFRASTRUCTURE NODE DRAIN EVICTIONS", "NODE_DRAIN", RED),
    ]

    for title, cat_key, cat_color in categories:
        cat_trials = [t for t in metrics.trials if t.scenario_category == cat_key]
        if not cat_trials:
            continue
        print(f"\n  {cat_color}{BOLD}▸ {title} ({len(cat_trials)} Trials){RESET}")
        print(f"    {DIM}{'Trial ID':<34} {'Service':<22} {'Drift (D)':<11} {'Exposure (ΔE)':<15} {'Status'}{RESET}")
        print(f"    {DIM}{'─' * 34} {'─' * 22} {'─' * 11} {'─' * 15} {'─' * 10}{RESET}")

        for t in cat_trials:
            d_pct = t.d_rate * 100
            e_pct = t.delta_e_rate * 100
            d_c = drift_color(t.d_rate)
            e_str = f"+{e_pct:.1f}%" if t.delta_e_rate > 0 else "0.0%"
            e_c = RED if t.delta_e_rate > 0 else DIM
            status_badge = f"{GREEN}✓ DONE{RESET}"
            
            print(f"    {WHITE}{t.trial_id:<34}{RESET} {CYAN}{t.target_service:<22}{RESET} {d_c}{BOLD}{d_pct:>5.1f}%{RESET}      {e_c}{e_str:>6}{RESET}          {status_badge}")

    # Footer Notes
    print(f"\n{DIM}{'─' * width}{RESET}")
    print(f"  {CYAN}💡 Interactive Web Dashboard:{RESET} http://127.0.0.1:8000/components/deployments")
    print(f"  {CYAN}📄 Raw Markdown Export:{RESET}      Run with {BOLD}--markdown{RESET} flag (python run_ensemble.py --markdown)")
    print(f"{DIM}{'─' * width}{RESET}\n")


if __name__ == "__main__":
    evaluator = ParametricEnsembleEvaluator()
    metrics = evaluator.run_ensemble()

    # Check for --markdown flag
    if "--markdown" in sys.argv or "-m" in sys.argv:
        report = ParametricEnsembleEvaluator.generate_markdown_report(metrics)
        print(report)
    else:
        render_cli_dashboard(metrics)
