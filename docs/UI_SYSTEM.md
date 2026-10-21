# UI_SYSTEM.md — Caliper (DRAFT for human approval)

Principle: calm, precise, readable. Like a measuring instrument: little decoration, clear state, one obvious next action.
English only. Light + dark themes. Server-rendered (Jinja2) + HTMX. No external fonts/CDNs (works offline).

## 1. Tokens

### Color (CSS variables; both themes defined)
| Token | Light | Dark |
|---|---|---|
| `--bg` | #FAFAF9 | #0F1115 |
| `--surface` | #FFFFFF | #171A21 |
| `--surface-2` | #F4F4F2 | #1E222B |
| `--border` | #E5E5E1 | #2A2F3A |
| `--text` | #1C1C1A | #ECECEA |
| `--text-muted` | #6B6B66 | #9A9AA3 |
| `--accent` (primary action only) | #3B5BDB | #7C93FF |
| `--danger` | #C92A2A | #FF6B6B |

### Request state colors (always paired with icon + text label, never color alone)
| State | Color | Icon |
|---|---|---|
| Planning | blue #2563EB | list |
| Executing | violet #7C3AED | code |
| Reviewing | cyan #0891B2 | search |
| Awaiting approval | amber #D97706 | hand (needs you) |
| Merged / Live | green #16A34A | check |
| Failed | red #DC2626 | x |

### Type
- Sans: `system-ui, -apple-system, "Segoe UI", sans-serif`. Mono (ids, code, diffs): `ui-monospace, "SF Mono", Menlo, monospace`.
- Scale: 12 (meta) / 14 (body, default) / 16 (emphasis) / 20 (section title) / 28 (page title). Weights: 400, 500 only.
- Line height 1.5 body, 1.3 titles.

### Space, shape, motion
- 4px grid: 4, 8, 12, 16, 24, 32, 48. Page content max-width 960px (diff view up to 1200px).
- Radius: 8px (cards, inputs, buttons), 999px (status pills).
- Borders 1px `--border`. No shadows except dialogs. No gradients.
- Motion: 120–180ms ease for hover/expand; none for state changes that matter. Respect `prefers-reduced-motion`.

## 2. Components (the ONLY ones allowed; extend this list via a Decision entry)
1. **Button**: primary (one per screen, `--accent`), secondary, ghost, danger. Min height 36px. Loading state built in.
2. **StatusPill**: dot/icon + label, state color.
3. **Card**: surface + border, 16px padding.
4. **Stepper**: Plan -> Execute -> Review -> Approve -> Dev -> Prod; current step highlighted; failed step red.
5. **Field**: label above, helper below, inline error. Text, textarea, select, segmented control, toggle.
6. **DiffView**: unified diff, mono, line numbers, collapsible files, per-file rule-violation badges.
7. **RuleRow**: rule ID + title + pass/fail + one-line reason; failed first.
8. **EmptyState**: icon, one sentence, one action.
9. **Toast**: bottom-right, auto-dismiss 4s, undo action when relevant.
10. **ConfirmDialog**: states exact consequence; destructive action is the right button, labeled with the verb.
11. **Sidebar**: project list, "+ New project", theme toggle.

## 3. Layout
- Left sidebar 240px (collapsible): projects. Main area: one column.
- Global shortcuts: `N` new request, `/` search, `Esc` close, `Cmd/Ctrl+Enter` submit forms. Shortcut hints shown in tooltips.
- Max 3 clicks from Dashboard to any action.

## 4. Screens

### 4.1 Dashboard
```
Sidebar           | Dashboard                          [ + New request ]
 Projects         |
  > shop-api      | NEEDS YOU (2)
  > billing       |  [Awaiting] Add refund endpoint        shop-api   Review ->
  + New project   |  [Awaiting] Fix rounding bug           billing    Review ->
                  | IN PROGRESS (1)
                  |  [Executing] Add audit log   ●●○○○○   shop-api
                  | RECENT
                  |  [Live] Fix login redirect   2h ago
```
Order: Needs you -> In progress -> Recent. If nothing needs the human, show "All clear".

### 4.2 Add project (one form, 3 fields)
```
New project
 Name            [ shop-api            ]
 Repository      ( ) Create new  ( ) Use existing URL [          ]
 Stack preset    [ Python + FastAPI  v ]
                                         [ Create project ]
```
Principles are pre-filled from the preset (editable later, from the project page). On success go straight to the project page with a prompt: "Make your first request".

### 4.3 New request (composer)
```
 [ Feature | Bug ]                 Project: shop-api v
 +--------------------------------------------------+
 | What do you want?                                |
 |                                                  |
 +--------------------------------------------------+
 + Add error / log / file (optional, collapsed)
                                        [ Submit  Ctrl+Enter ]
```
One box. Nothing else required. After submit, go to the request page showing live progress.

### 4.4 Request page / approval
```
 Add refund endpoint        [Awaiting approval]
 Plan > Execute > Review > [Approve] > Dev > Prod
 ----------------------------------------------
 Review: 2 failed, 14 passed
  FAIL  SEC-003  Input not validated in refund.py:41
  FAIL  PRF-002  N+1 query in list_refunds
  (14 passed - show)
 Plan (collapsed)      Changes (3 files, expanded)
 ----------------------------------------------
 [sticky bar]   [ Request changes ]   [ Approve & merge ]
```
Failed rules first. "Request changes" opens one text box; the request returns to Execution with that note.

### 4.5 Project page
Tabs-free single page: requests list, Principles (6 cards, editable), Docs (manifest, Graph from graph.yaml, Decisions timeline, sync/init/add-decision actions), Environments (Dev/Prod status + last deploy).

## 5. Content rules
- Sentence case. Verbs on buttons ("Approve & merge", not "OK").
- Errors: what happened + what to do. Never raw stack traces (offer "Show details").
- Times relative ("2h ago") with absolute time on hover.

## 6. Accessibility
- Contrast AA minimum. Visible focus ring (2px `--accent`). All actions keyboard reachable.
- Status never conveyed by color alone.
