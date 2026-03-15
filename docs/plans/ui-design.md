# CV Explorer — UI Design

Reference: [Phase 1 Design](../phase1-design.md) | [Implementation Plan](phase1-implementation.md)

## Design Philosophy

Modern dark-themed data labeling tool that feels native to the Databricks platform. Borrows the best patterns from FiftyOne (modern dark aesthetic), Labelbox (annotation UX), and CVAT (canvas precision) while matching the Databricks workspace dark mode palette.

## Color Palette — Databricks Dark Mode

Colors extracted from the Databricks dark mode UI (`docs.databricks.com` dark theme + workspace CSS tokens).

### Core Backgrounds

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-primary` | `#1b1b1d` | Page background, main content area |
| `--bg-secondary` | `#242526` | Navbar, elevated surfaces, input backgrounds |
| `--bg-card` | `#1e1f21` | Cards, panels, dropdown menus |
| `--bg-hover` | `#2a2b2d` | Hover states, table row hover |
| `--bg-input` | `#242526` | Form inputs, search bars |
| `--sidebar-bg` | `#171717` | Sidebar background |

### Text

| Token | Hex | Usage |
|-------|-----|-------|
| `--text-primary` | `#e3e3e3` | Body text, labels |
| `--text-heading` | `#fafafa` | Headings, emphasis |
| `--text-secondary` | `#a1a1a1` | Muted text, descriptions, timestamps |
| `--text-muted` | `#686868` | Disabled text, placeholders |

### Accent Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `--accent-blue` | `#4299e0` | Primary accent — links, active nav items, focus rings |
| `--accent-blue-dark` | `#2272b4` | CTA buttons, primary actions |
| `--accent-blue-light` | `#72a1ed` | Hover state for links |
| `--accent-red` | `#eb1600` | Databricks brand red — logo, brand moments only |

### Status Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `--status-success` | `#009400` | Labeled items, success states |
| `--status-warning` | `#e6a700` | Skipped items, warnings |
| `--status-error` | `#e13238` | Errors, destructive actions |
| `--status-info` | `#4cb3d4` | Info callouts, tips |

### Borders & Surfaces

| Token | Hex | Usage |
|-------|-----|-------|
| `--border-color` | `#ffffff1a` | Default borders (10% white) |
| `--border-hover` | `#ffffff33` | Border hover state (20% white) |
| `--border-focus` | `#4299e0` | Focus ring, active borders |
| `--scrollbar-track` | `#444444` | Scrollbar track |
| `--scrollbar-thumb` | `#686868` | Scrollbar thumb |

### Semantic Backgrounds (for callouts/badges)

| Token | Hex | Usage |
|-------|-----|-------|
| `--badge-blue-bg` | `rgba(66, 153, 224, 0.15)` | Classification badge bg |
| `--badge-blue-text` | `#72a1ed` | Classification badge text |
| `--badge-green-bg` | `rgba(0, 148, 0, 0.15)` | Labeled status badge bg |
| `--badge-green-text` | `#00c853` | Labeled status badge text |
| `--badge-yellow-bg` | `rgba(230, 167, 0, 0.15)` | Skipped/warning badge bg |
| `--badge-yellow-text` | `#ffa726` | Skipped/warning badge text |

---

## Typography

- Font stack: `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
- Base size: `14px` (matches Databricks workspace)
- Line height: `1.5`
- Weights: 400 (body), 500 (labels/nav), 600 (buttons), 700 (headings/metrics)

---

## Page Layouts

### 1. Projects List (`/`)

```
┌──────────────────────────────────────────────────────────────────┐
│  [Logo]  CV Explorer                            [user@email]     │
├───────┬──────────────────────────────────────────────────────────┤
│       │  Projects                        [+ Create Project]     │
│  Nav  │─────────────────────────────────────────────────────────│
│       │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│ Proj  │  │ Project A │  │ Project B │  │ Project C│              │
│ ects  │  │ classif.  │  │ detection │  │ classif. │              │
│       │  │ ████░░ 60%│  │ ██░░░░ 25%│  │ █░░░░░ 8%│              │
│       │  │ 300/500   │  │ 50/200    │  │ 40/500  │              │
│       │  │ by brian  │  │ by sarah  │  │ by brian │              │
│       │  └──────────┘  └──────────┘  └──────────┘              │
│       │                                                          │
└───────┴──────────────────────────────────────────────────────────┘
```

- Grid of project cards (responsive: 1-3 columns)
- Each card: name, task type badge, progress bar, sample counts, creator
- Click card → project dashboard
- Minimal sidebar: just Projects + link to Databricks workspace

### 2. Create Project (`/projects/new`)

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Projects                                              │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Create New Project                                               │
│                                                                   │
│  Project Name  ┌──────────────────────────┐                      │
│                └──────────────────────────┘                      │
│  Description   ┌──────────────────────────┐                      │
│                └──────────────────────────┘                      │
│                                                                   │
│  Task Type     (○) Classification  (○) Detection                 │
│                                                                   │
│  Source Volume  ┌──────────────────── [Browse] ┐                 │
│                 │ /Volumes/catalog/schema/vol   │                 │
│                 └──────────────────────────────┘                 │
│                                                                   │
│  Classes       ┌──────────┐  [+ Add]                             │
│                │ cat  [×]  │                                      │
│                │ dog  [×]  │                                      │
│                │ car  [×]  │                                      │
│                └──────────┘                                      │
│                                                                   │
│  Preview (from selected volume)                                   │
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐                             │
│  │    │ │    │ │    │ │    │ │    │                               │
│  └────┘ └────┘ └────┘ └────┘ └────┘                             │
│                                                                   │
│                             [Cancel]  [Create Project]            │
└──────────────────────────────────────────────────────────────────┘
```

- Clean form layout, centered, max-width ~600px
- Volume browser reused from existing `BrowseVolumes.jsx`
- Dynamic class list editor (type + Enter to add, × to remove)
- Image preview strip from selected volume (5 random thumbnails)

### 3. Labeling View (`/projects/:id/label`)

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Project Name          42 / 500 labeled  ████████░░░░  [⚙]   │
├──────────────────────────────────────────────────────────────────┤
│                                                │                 │
│                                                │  Classification │
│                                                │  ─────────────  │
│                                                │                 │
│           ┌─────────────────────┐              │  [1] cat        │
│           │                     │              │  [2] dog        │
│           │                     │              │  [3] car        │
│           │    Current Image    │              │  [4] truck      │
│           │                     │              │                 │
│           │                     │              │  ─────────────  │
│           │                     │              │  [S] Skip       │
│           └─────────────────────┘              │                 │
│                                                │  ─────────────  │
│                                                │  filename.jpg   │
│                                                │  1024 × 768     │
│   [← Prev]                        [Next →]    │  locked by: you │
│                                                │                 │
└────────────────────────────────────────────────┴─────────────────┘
```

*Layout zones:*
- *Top bar:* back arrow, project name, progress counter + bar, settings gear
- *Center (75%):* large image display, maximized. For detection: bounding box canvas overlay
- *Right panel (25%):* class buttons (numbered 1-9), skip button, file info, lock status
- *Bottom:* prev/next navigation arrows

*Keyboard shortcuts:*
- `1`-`9`: Select class label (matches button order)
- `→` / `Enter`: Next sample
- `←`: Previous sample
- `S`: Skip current sample
- `Esc`: Return to project dashboard

*Classification mode:*
- Large buttons in right panel, one per class
- Click or press number key → annotate + auto-advance to next
- Active/selected class highlighted with blue accent

*Detection mode:*
- Right panel shows class selector + annotation list
- Click-and-drag on image to draw bounding box
- Box snaps to selected class color
- List of drawn boxes in right panel with delete button per box
- Save button to commit all boxes

### 4. Project Dashboard (`/projects/:id`)

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Projects                                   [Start Labeling]  │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Project Name                                                     │
│  Description text here                                            │
│  Classification · Created by brian · Mar 15, 2026                │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │   500    │  │   300    │  │    50    │  │   150    │         │
│  │  Total   │  │ Labeled  │  │ Skipped  │  │Unlabeled │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│                                                                   │
│  Progress  ██████████████████████░░░░░░  70%                     │
│                                                                   │
│  ┌─────────────────────────────────────────────────────┐         │
│  │  Contributor          Labeled    Skipped             │         │
│  │  brian@databricks.com    200        30               │         │
│  │  sarah@databricks.com   100        20               │         │
│  └─────────────────────────────────────────────────────┘         │
│                                                                   │
│  Lakehouse Sync: ● Active                                        │
│  Delta tables: brian_gen_ai.cv_explorer.lb_annotations_history   │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

- Stats cards row with large metric numbers (blue accent)
- Full-width progress bar
- Contributor table
- Lakehouse Sync status indicator (green dot = active, gray = not configured)
- Link to Delta table location in Unity Catalog

---

## Component Patterns

### Cards
- Background: `--bg-card` (`#1e1f21`)
- Border: 1px solid `--border-color` (`rgba(255,255,255,0.1)`)
- Border-radius: `8px` (matching Databricks rounded corners)
- Hover: border → `--border-hover`, subtle blue shadow
- No dramatic elevation — Databricks uses flat design with subtle borders

### Buttons
- Primary: `--accent-blue-dark` (`#2272b4`) bg, white text, rounded 6px
- Primary hover: `--accent-blue` (`#4299e0`)
- Secondary: transparent bg, `--border-color` border, `--text-primary` text
- Danger: `--status-error` bg, white text

### Active/Selected States
- Active nav item: `--accent-blue` text, `rgba(255,255,255,0.05)` bg, font-weight 700
- Selected card: `--accent-blue` border
- Focus rings: 2px solid `--accent-blue`

### Progress Bars
- Track: `--bg-secondary` (`#242526`)
- Fill: gradient from `--accent-blue-dark` to `--accent-blue`
- Height: 6px, border-radius: 3px

### Badges
- Pill-shaped (border-radius: 9999px)
- Semi-transparent background tinted by status color
- Font-size: 0.75rem, font-weight: 600

### Tables
- Header: `--bg-secondary` bg, `--text-secondary` text, uppercase, 0.7rem
- Rows: transparent bg, `--border-color` bottom border
- Hover: `--bg-hover` bg
- Stripe: `rgba(255,255,255,0.07)` alternating rows

---

## Sidebar

- Width: 220px (narrower than current 260px — Databricks uses compact sidebars)
- Background: `--sidebar-bg` (`#171717`)
- Logo area: Databricks diamond + "CV Explorer" in `--text-heading`
- Nav items: `--text-primary`, 14px, icon + label
- Active item: `--accent-blue` text, left 3px blue border accent
- Hover: `rgba(255,255,255,0.05)` bg
- Bottom: user email, link to Databricks workspace

---

## Responsive Behavior

- Desktop (>1200px): sidebar + full layout
- Tablet (768-1200px): collapsible sidebar, 2-column project grid
- Labeling view: right panel collapses to bottom on narrow screens

---

## Migration from Current Theme

Current → New mapping:

| Current Token | Current Value | New Value | Notes |
|--------------|---------------|-----------|-------|
| `--bg-primary` | `#1a2332` (navy) | `#1b1b1d` (neutral dark) | Databricks uses neutral grays, not blue-tinted |
| `--bg-secondary` | `#1e2a3a` | `#242526` | Lighter surface |
| `--bg-card` | `#243044` | `#1e1f21` | Darker, more subtle cards |
| `--bg-hover` | `#2a3a50` | `#2a2b2d` | Neutral hover |
| `--bg-input` | `#1e2a3a` | `#242526` | Match surface color |
| `--accent-teal` | `#00b4d8` | `#4299e0` | Blue replaces teal (Databricks primary) |
| `--accent-teal-light` | `#48cae4` | `#72a1ed` | |
| `--accent-teal-dark` | `#0096c7` | `#2272b4` | |
| `--text-primary` | `#e8edf3` | `#e3e3e3` | Slightly warmer |
| `--text-secondary` | `#8a99ab` | `#a1a1a1` | Neutral gray |
| `--text-muted` | `#5a6a7d` | `#686868` | Neutral gray |
| `--border-color` | `#2a3a50` | `#ffffff1a` | Semi-transparent white |
| `--sidebar-bg` | `#152030` | `#171717` | Nearly black |

Key shift: *from blue-tinted navy to neutral charcoal grays*, matching Databricks' actual dark mode which uses pure grays (`#1b1b1d`, `#242526`, `#171717`) rather than any color-tinted backgrounds.
