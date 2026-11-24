---
name: Support Desk Operations Console
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3e4947'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#6e7977'
  outline-variant: '#bdc9c6'
  surface-tint: '#006a63'
  primary: '#005c55'
  on-primary: '#ffffff'
  primary-container: '#0f766e'
  on-primary-container: '#a3faef'
  inverse-primary: '#80d5cb'
  secondary: '#0051d5'
  on-secondary: '#ffffff'
  secondary-container: '#316bf3'
  on-secondary-container: '#fefcff'
  tertiary: '#7d4200'
  on-tertiary: '#ffffff'
  tertiary-container: '#a15600'
  on-tertiary-container: '#ffe6d5'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#9cf2e8'
  primary-fixed-dim: '#80d5cb'
  on-primary-fixed: '#00201d'
  on-primary-fixed-variant: '#00504a'
  secondary-fixed: '#dbe1ff'
  secondary-fixed-dim: '#b4c5ff'
  on-secondary-fixed: '#00174b'
  on-secondary-fixed-variant: '#003ea8'
  tertiary-fixed: '#ffdcc3'
  tertiary-fixed-dim: '#ffb77d'
  on-tertiary-fixed: '#2f1500'
  on-tertiary-fixed-variant: '#6e3900'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 1.5rem
    fontWeight: '600'
    lineHeight: 2rem
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 1.25rem
    fontWeight: '600'
    lineHeight: 1.75rem
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '600'
    lineHeight: 1.5rem
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '500'
    lineHeight: 1.375rem
  body-md:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.25rem
  body-sm:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
  label-lg:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '600'
    lineHeight: 1rem
    letterSpacing: 0.02em
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 0.75rem
    fontWeight: '500'
    lineHeight: 1rem
    letterSpacing: -0.01em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '400'
    lineHeight: 0.875rem
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-compact: 0.5rem
  margin: 1.5rem
  margin-sm: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system serves high-velocity compliance officers, risk operators, and customer support engineers navigating Indian financial markets (SEBI, RBI, KYC, and AML audit frameworks). The emotional baseline is unflinching composure: calm under transaction volume spikes, ruthlessly transparent in auditability, and devoid of cosmetic friction.

The aesthetic fuses **Utilitarian Minimalism** with **Engineered Density**—drawing inspiration from systems like Linear, Stripe Dashboard, and Vercel. Layouts treat data density not as clutter, but as clarity. High-contrast hairline borders, monospaced tracking identifiers, and muted tonal containers present deep operational context at a glance without triggering visual exhaustion over eight-hour operational shifts.

Visual hierarchy is communicated through precision typography, subtle state tints, and tabular alignment rather than decorative flourishes.

## Colors

The palette is engineered for precision, status legibility, and visual endurance during sustained monitoring.

- **Canvas & Structural Neutrals**: The foundation utilizes an off-white slate base (`#F8FAFC`) with pure white (`#FFFFFF`) dedicated to raised operational surfaces, data tables, and modal sheets. Borders rely uniformly on hairline slate (`#E2E8F0`), with secondary grid lines resting at `#F1F5F9`.
- **Primary Operational Accent**: Deep Teal (`#0F766E`, Teal-700) anchors active navigation, confirmed states, primary actions, and validated ledger trails. An elevated teal hover (`#115E59`) and faint container wash (`#F0FDFA`) reinforce interactive elements without overwhelming the viewport.
- **Status Semantics (Compliance & Audit)**:
  - **Success / Audit Passed**: Forest Green (`#16A34A`), container fill (`#F0FDF4`), border tint (`#BBF7D0`).
  - **Warning / Compliance Drift / Review Needed**: Amber (`#D97706`), container fill (`#FFFBEB`), border tint (`#FDE68A`).
  - **Critical / Hard-Fail / Blocked Account**: Crimson Red (`#DC2626`), container fill (`#FEF2F2`), border tint (`#FECACA`).
  - **Informational / System Telemetry**: Cobalt Blue (`#2563EB`), container fill (`#EFF6FF`), border tint (`#BFDBFE`).
- **Text & Data Contrast**: Primary values and entity headers render in slate-900 (`#0F172A`), structural descriptions in slate-600 (`#475569`), and inactive/metadata values in slate-400 (`#94A3B8`).

## Typography

Typography enforces rigid tabular scans and zero ambiguity between alphanumeric characters.

- **Primary Interface (Inter)**: Handles system navigation, ticket titles, audit logs, and contextual narrative. All numbers inside tables must enforce tabular figures (`font-variant-numeric: tabular-nums`) to prevent horizontal jitter during live balance reconciliations and timestamp updates.
- **Identifiers & Telemetry (JetBrains Mono)**: Mandatory for all entity hashes, SEBI order IDs, ticket identifiers (e.g., `tkt_01J...`, `rev_01J...`), PAN masks, IFSC routing numbers, and micro-timestamps.
- **Micro-sizing for Information Density**: The core working typography hovers tightly between 11px (`label-sm`) and 13px (`body-md`), ensuring high line-item visibility above the fold without sacrificing legibility or touch target ergonomics.

## Layout & Spacing

The layout is built upon an uncompromising 8px baseline grid with strict 4px sub-intervals for dense data grouping.

- **Grid Architecture**: Standard viewports utilize an asymmetric operations layout: a fixed 240px utility sidebar, an optional collapsible 320px master list panel, and a flexible primary review workbench with a max-width of `1600px`.
- **Vertical Spacing Discipline**: Section margins are pinned to `space-xl` (24px) down to `space-md` (12px). Table rows maintain a compact 36px fixed height (44px for high-density touch/tablet inspection).
- **Responsive Adaptations**:
  - **Desktop (≥ 1280px)**: Persistent split-pane views; side-by-side transaction log and audit confirmation drawer.
  - **Laptop / Compact Desktop (1024px – 1279px)**: Third-column audit panel defaults to a 380px overlay sheet.
  - **Tablet / Mobile (≤ 1023px)**: Single-column workflow with persistent sticky status footers and horizontal scroll for tabular feeds.

## Elevation & Depth

Visual separation relies on crisp containment, boundary contrast, and subtle micro-shadows rather than diffuse, floating elevation.

- **Borders over Shadows**: Spatial hierarchy is driven by crisp 1px borders (`#E2E8F0`). Nested sections use alternating background tints (`#F8FAFC` to `#FFFFFF`) to express grouping without vertical elevation.
- **Micro-Shadows**:
  - **Level 0 (Flat)**: Embedded data tables, structural cards, split view panels: `none`, border: `1px solid #E2E8F0`.
  - **Level 1 (Interactive / Elevated Card)**: Hovered ticket rows, dropdown menus, filter popovers: `0 1px 2px 0 rgba(15, 23, 42, 0.05)`, border: `1px solid #CBD5E1`.
  - **Level 2 (Modals / Overlays)**: KYC document inspection lightboxes, compliance sanction panels: `0 4px 6px -1px rgba(15, 23, 42, 0.08), 0 2px 4px -2px rgba(15, 23, 42, 0.04)`.
- **Glass & Backdrop**: Modal sheets and sticky table headers incorporate subtle background blur (`backdrop-filter: blur(8px)`) over an 85% alpha background (`rgba(255, 255, 255, 0.85)`).

## Shapes

The interface embraces a uniform 8px (`rounded-md`) standard across containers, dialogs, and primary inputs to maintain geometric rhythm.

- **Primary Geometry**: Standard cards, table frames, popovers, and interactive buttons conform to `0.5rem` (8px).
- **Internal Micro-elements**: Form controls, inline filter chips, and segmented controllers use `0.375rem` (6px) to maintain nested proportion inside parent containers.
- **Monospace Identifiers & Status Pills**: Status badges and ID chips use `0.25rem` (4px) or full pill radius (`9999px`) strictly when communicating categorical state indicators (e.g., `AML Cleared`, `SEBI Flagged`).

## Components

### Buttons & Action Triggers
- **Primary Action (Commit / Approve)**: Deep Teal background (`#0F766E`), white text (`#FFFFFF`), 1px solid `#0D9488` border, subtle inset highlight `0 1px 0 0 rgba(255, 255, 255, 0.15)`. Height: 32px (compact) or 36px (regular). Text: `Inter`, 13px, weight 500.
- **Secondary Action (Filter / Export)**: Pure white background (`#FFFFFF`), slate-700 text (`#334155`), 1px solid `#E2E8F0` border. Hover: `#F8FAFC`.
- **Destructive Action (Block / Reject KYC)**: Soft red container (`#FEF2F2`), crimson text (`#DC2626`), 1px solid `#FECACA` border. Hover: `#FEE2E2`.

### Chips & Monospace ID Badges
- **Identifier Chips (`tkt_01J...`, `ord_99A...`)**: Inset background (`#F1F5F9`), slate-600 text (`#475569`), hairline border (`#E2E8F0`), padding `1px 6px`, radius 4px. Rendered strictly in `JetBrains Mono` 11px. Click-to-copy utility on hover with micro-tooltip feedback.
- **Status Badges**:
  - *Active Review*: Amber surface (`#FFFBEB`), amber text (`#B45309`), border `#FDE68A`, accompanied by a solid 6px status dot.
  - *Compliant*: Green surface (`#F0FDF4`), green text (`#15803D`), border `#BBF7D0`.
  - *Escalated*: Red surface (`#FEF2F2`), red text (`#B91C1C`), border `#FECACA`.

### Form Inputs & Filters
- **Input Fields**: Height: 32px. Hairline border `#CBD5E1`, background `#FFFFFF`. Focus ring: 2px deep teal ring at 20% opacity (`rgba(15, 118, 110, 0.2)`) with a `#0F766E` active border. Text: 13px Inter. Placeholder: `#94A3B8`.
- **Checkboxes & Radios**: 16px × 16px square/circle with 1px border (`#CBD5E1`). Checked state fills with `#0F766E` featuring a crisp white geometric checkmark.

### Tabular Ledger & Audit Cards
- **Data Tables**: Zero-margin outer border container with zebra row alternation (`#FFFFFF` to `#F8FAFC/50`). Row headers render in 11px uppercase `Inter` (`#64748B`) with explicit sort indicators. Hovering over a row initiates an instant background fill of `#F0FDFA/40` (light teal tint).
- **Audit Cards**: Information grouping boxes with a solid 1px border (`#E2E8F0`), a distinct title header strip (`#F8FAFC`, 32px height), and structured key-value rows arranged in 2-column or 4-column balanced grids.