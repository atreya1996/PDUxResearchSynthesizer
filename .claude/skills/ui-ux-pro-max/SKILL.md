---
name: ui-ux-pro-max
description: "UI/UX design intelligence for web and mobile. Includes 50+ styles, 161 color palettes, 57 font pairings, 161 product types, 99 UX guidelines, and 25 chart types across 10 stacks (React, Next.js, Vue, Svelte, SwiftUI, React Native, Flutter, Tailwind, shadcn/ui, and HTML/CSS). Actions: plan, build, create, design, implement, review, fix, improve, optimize, enhance, refactor, and check UI/UX code. Projects: website, landing page, dashboard, admin panel, e-commerce, SaaS, portfolio, blog, and mobile app. Elements: button, modal, navbar, sidebar, card, table, form, and chart. Styles: glassmorphism, claymorphism, minimalism, brutalism, neumorphism, bento grid, dark mode, responsive, skeuomorphism, and flat design. Topics: color systems, accessibility, animation, layout, typography, font pairing, spacing, interaction states, shadow, and gradient."
---

# UI/UX Pro Max — Design Intelligence (Streamlit Edition)

Comprehensive design guide for dashboards and data-heavy web applications.
For this project (`PDUxResearchSynthesizer`), apply guidance to:
- `.streamlit/config.toml` — primaryColor, backgroundColor, secondaryBackgroundColor, textColor, font
- Plotly color sequences in `app.py` charts
- Layout decisions (column ratios, sidebar width, metric card groupings)

## When to Apply

Must invoke when:
- Designing or refactoring any page in `app.py`
- Choosing a color palette or chart color sequence
- Reviewing the dashboard for UX / accessibility issues

## Design System Workflow for This Project

### Step 1: Generate Design System

Run the search script to get a tailored design system:
```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py \
  "ux research dashboard financial inclusion analytics data-dense" \
  --design-system -p "PDUxResearchSynthesizer"
```

### Step 2: Apply to Streamlit Theme

Create `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "<from design system>"
backgroundColor = "<from design system>"
secondaryBackgroundColor = "<from design system>"
textColor = "<from design system>"
font = "sans serif"
```

### Step 3: Apply Chart Colors

Replace default Plotly color sequences with the palette from the design system:
```python
COLOR_SEQ = ["#...", "#...", "#..."]  # from design system
fig = px.bar(..., color_discrete_sequence=COLOR_SEQ)
```

## Priority Rules (Dashboard Context)

| Priority | Rule | Application |
|----------|------|-------------|
| 1 | Accessibility contrast ≥4.5:1 | All text on chart backgrounds |
| 2 | Chart legends always visible | Never hide Plotly legends |
| 3 | Consistent color palette | Same COLOR_SEQ across all charts |
| 4 | Data-dense layout | Use st.columns() to pack metrics |
| 5 | Empty state handling | Show st.info() when df is empty |
| 6 | Responsive containers | Always pass use_container_width=True |
| 7 | Loading feedback | Use st.spinner() for all API calls |

## Quick Reference: Streamlit UX Anti-Patterns

- Using `unsafe_allow_html=True` with dynamic content → XSS risk
- Calling `st.rerun()` inside a loop → infinite loop
- Missing `st.session_state` guard on Gemini buttons → double API call
- Raw hex colors hardcoded per-chart → inconsistent palette
- `st.dataframe` without `use_container_width=True` → truncated on mobile
