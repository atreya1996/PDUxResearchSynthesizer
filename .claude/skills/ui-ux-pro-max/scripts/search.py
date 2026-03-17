#!/usr/bin/env python3
"""
UI/UX Pro Max — Design System Search CLI
Stub implementation. Full version available at:
  https://github.com/nextlevelbuilder/ui-ux-pro-max-skill

Usage:
  python3 search.py "<query>" --design-system [-p "Project Name"]
  python3 search.py "<query>" --domain <domain> [-n <max_results>]

Install the full version:
  npm install -g uipro-cli
  uipro init --ai claude
"""
import argparse
import sys


DESIGN_SYSTEM_TEMPLATE = """
╔══════════════════════════════════════════════════════════════════════╗
║  UI/UX Pro Max — Design System for: {project}
╠══════════════════════════════════════════════════════════════════════╣
║  Query: {query}
╠══════════════════════════════════════════════════════════════════════╣
║  STYLE         Minimal Data Dashboard
║  PATTERN       Bento Grid / Card Layout
╠══════════════════════════════════════════════════════════════════════╣
║  COLORS
║    Primary     #1A73E8  (Google Blue — trust, reliability)
║    Secondary   #34A853  (Green — positive financial signals)
║    Accent      #FBBC04  (Amber — alerts, needs_reprocessing)
║    Background  #F8F9FA  (Light grey — data-dense readability)
║    Surface     #FFFFFF  (Card backgrounds)
║    Text        #202124  (Near-black — high contrast)
║    Muted       #5F6368  (Secondary labels)
╠══════════════════════════════════════════════════════════════════════╣
║  TYPOGRAPHY
║    Font        Inter / system-ui (Streamlit: sans serif)
║    Body        16px / line-height 1.6
║    Heading     24px Bold
║    Label       13px Medium, letter-spacing 0.02em
╠══════════════════════════════════════════════════════════════════════╣
║  CHART PALETTE (Plotly)
║    px.colors.qualitative.Set2  — categorical data
║    ["#1A73E8","#34A853","#FBBC04","#EA4335","#9C27B0"]  — custom seq
╠══════════════════════════════════════════════════════════════════════╣
║  STREAMLIT CONFIG (.streamlit/config.toml)
║    primaryColor           = "#1A73E8"
║    backgroundColor        = "#F8F9FA"
║    secondaryBackgroundColor = "#FFFFFF"
║    textColor              = "#202124"
║    font                   = "sans serif"
╠══════════════════════════════════════════════════════════════════════╣
║  ANTI-PATTERNS
║    - Mixed chart color families across views
║    - Pie charts with >5 categories (use bar instead)
║    - Missing st.spinner() on API calls
║    - Hardcoded hex per-chart instead of shared COLOR_SEQ constant
╚══════════════════════════════════════════════════════════════════════╝

NOTE: This is a stub. Install the full skill for 161 palettes + reasoning:
  npm install -g uipro-cli && uipro init --ai claude
"""

UX_DOMAIN_RESULTS = [
    "accessibility: Contrast ratio ≥4.5:1 for all text",
    "accessibility: Focus rings on all interactive elements",
    "loading: Use st.spinner() for operations >300ms",
    "empty-state: Show helpful message when DataFrame is empty",
    "chart: Always pass use_container_width=True to plotly_chart",
    "chart: Legends must be visible and not hidden",
    "layout: Mobile-first column ratios (single col on small screens)",
    "form: Visible labels — never placeholder-only inputs",
    "animation: Keep transitions ≤300ms",
    "feedback: Toast/success message after save actions",
]


def main():
    parser = argparse.ArgumentParser(description="UI/UX Pro Max Design Search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--design-system", action="store_true")
    parser.add_argument("--domain", default=None)
    parser.add_argument("-p", "--project", default="My Project")
    parser.add_argument("-n", "--max-results", type=int, default=5)
    parser.add_argument("-f", "--format", default="ascii", choices=["ascii", "markdown"])
    args = parser.parse_args()

    if args.design_system:
        print(DESIGN_SYSTEM_TEMPLATE.format(project=args.project, query=args.query))
    elif args.domain:
        print(f"\n=== Domain: {args.domain} | Query: {args.query} ===\n")
        results = [r for r in UX_DOMAIN_RESULTS if args.domain in r] or UX_DOMAIN_RESULTS
        for i, r in enumerate(results[: args.max_results], 1):
            print(f"  {i}. {r}")
        print()
    else:
        print(f"Results for '{args.query}':")
        for i, r in enumerate(UX_DOMAIN_RESULTS[: args.max_results], 1):
            print(f"  {i}. {r}")


if __name__ == "__main__":
    main()
