# UI_SYSTEM.md — Caliper (v2, the only UI spec)

Status: approved as the target spec on 2026-10-22. The current Jinja2/vanilla-JS UI is transitional and is replaced by this port (D-044). No new Jinja pages are built.

Stack: React + TypeScript + Vite, Tailwind CSS, shadcn/ui, lucide-react icons. English only. Light and dark themes (follow system, user can override).
Style: neutral, dense, quiet. Closer to Linear/Vercel dashboards than to a marketing site.

## 1. Rules
- Components come from shadcn/ui. Add a component with the shadcn CLI; do not hand-roll what shadcn provides.
- No emoji anywhere (UI, copy, logs shown to users). Icons are lucide only, 16px inline, 20px in empty states.
- No filler copy. No greetings, no "Welcome", no explanatory paragraphs. Labels are nouns or verbs. Helper text is one short line and only when the field is not self-evident.
- One primary button per screen. Everything else is secondary or ghost.
- Color carries meaning only: state badges and destructive actions. Everything else is neutral.

## 2. Tokens (shadcn theme variables, neutral base)
- Base color: `neutral` (shadcn). Radius: `0.5rem`. Font: Geist Sans or system-ui (self-hosted, no CDN); mono: Geist Mono or ui-monospace.
- Type scale: 12 (meta), 14 (body, default), 16 (emphasis), 20 (page title). Weights 400/500/600.
- Spacing: Tailwind 4px scale. Page max-width 1100px; diff view full width.
- Borders over shadows. No gradients.

### Request state badges (Badge component, always icon + label)
| State | Color | Icon |
|---|---|---|
| Planning | blue | ListTodo |
| Executing | violet | Code2 |
| Reviewing | cyan | SearchCheck |
| Awaiting approval | amber | Hand |
| Merged / Live | green | CheckCircle2 |
| Failed | red | XCircle |

## 3. shadcn components used
Button, Badge, Card, Input, Textarea, Select, Switch, ToggleGroup, Form (react-hook-form + zod), Label, Tabs, Table, Dialog, AlertDialog, Sheet, Tooltip, Sonner (toasts), Skeleton, Separator, Collapsible, ScrollArea, Command (palette), Sidebar, Progress, Alert.
Custom (built from the above): StatusBadge, Stepper, DiffView, RuleRow, SetupChecklist, SecretField, ConnectionTest.

## 4. Layout
- shadcn Sidebar (collapsible): Dashboard, Projects (list + add), Settings (Models). Theme toggle at the bottom.
- Page header: title left, primary action right. Content below in Cards or Tables.
- `Cmd/Ctrl+K` command palette: jump to project, new request. `N` new request. `Esc` closes. `Cmd/Ctrl+Enter` submits.
- Max 3 clicks to any action.

## 5. Screens

### Dashboard
Header: "Dashboard" + `New request`.
Table sections in order: Needs approval, In progress, Recent. Columns: title, project, StatusBadge, updated (relative). Row click opens the request. If nothing needs approval, the section is hidden (no "all clear" text).
Setup banner (Alert, only if something is unconfigured): "Models not configured" with a `Configure` link.

### New project (Dialog)
Fields: Name, GitHub repo URL. Button: `Create`. After creation open the project page.

### Project page
Header: name, repo, `New request`.
Tabs: Requests | Principles | Environments | Decisions | Graph | Settings.
When setup is incomplete, a SetupChecklist Card sits above the tabs:
- Repository access: public key (SecretField with copy), link to deploy keys page, `Test connection`.
- Principles: six rows, each with status (Draft / Approved) and `Edit`.
- Dev environment, Prod environment: status and `Configure`.
Each row shows Connected / Not connected with a StatusBadge; failing tests show the exact error and a one-line fix.

### Project Settings tab, Delete project
Danger zone Card at the bottom of Settings: `Delete project` (destructive outline button). The sidebar project menu has the same item.
AlertDialog contents:
- Title: "Delete {project name}"
- Will be removed: N requests and their history, local clone and worktrees, deploy key and environment keys.
- Will stay: the GitHub repository and the `.caliper/` files in it.
- Switches, off by default: "Stop Dev deployment", "Stop Prod deployment" (Prod switch has an inline warning). Volumes are never deleted.
- Active requests, if any, are listed with "will be cancelled".
- Input: type the project name to confirm. Destructive button `Delete project` is enabled only on exact match.
- On failure (for example an unreachable host while stopping Dev): Alert with the exact error, nothing is deleted, the dialog stays open.
- On success: toast "Project deleted", navigate to Dashboard. One-line note to remove the deploy key in the repo's GitHub settings.

### Settings > Models
Three Cards (Planning, Execution, Review). Fields: Base URL, Model, API key. `Test` per card shows latency and a sample response status. Inline error if two roles use the same model.

### New request
Project select (preselected), ToggleGroup Feature | Bug, Textarea, optional Collapsible for logs/files, `Submit`. If setup is incomplete the button is disabled with a Tooltip naming the missing item.

### Request page
Header: title, StatusBadge. Stepper: Plan, Execute, Review, Approve, Dev, Prod.
Body: review verdict (RuleRow list, failed first), Plan (Collapsible), Changes (DiffView).
Sticky footer: `Request changes` (secondary), `Approve and merge` (primary). Request changes opens a Dialog with one Textarea.
Live progress via SSE, no polling spinners.

## 6. States
- Loading: Skeleton, never a blank page.
- Empty: icon + one line + one action (e.g. "No requests" + `New request`).
- Error: Alert with what failed and the next action; `Show details` collapsible for raw output.
- Destructive: AlertDialog naming the exact consequence; confirm button uses the verb.

## 7. Accessibility
Keyboard reachable, visible focus ring, AA contrast, state never conveyed by color alone (shadcn defaults satisfy most of this; do not override them).

## 8. Motion
Library: `motion` (React). Tailwind transitions and `tw-animate-css` (shadcn default) for simple hover/enter states. Motion is for state, not decoration: every animation answers "what just changed" or "what is happening now".

### Budget
- Durations: 120-200ms for hover/press/toggle, 200-300ms for enter/exit and layout, 600-900ms only for artifact travel along the pipeline.
- Easing: ease-out for enter, ease-in-out for movement. Layout changes use a spring (stiffness 400, damping 35).
- Animate `transform` and `opacity` only. Never animate width/height/top/left (exception: the progress bar width).
- Looping animation only for work in progress: at most one pulsing element per region.
- `prefers-reduced-motion`: replace movement with instant state change or a 100ms opacity fade; no loops.
- Motion never blocks input. Nothing waits for an animation to finish before accepting a click.

### Required animations
1. **Factory floor** (request page header and dashboard row expansion): Planning, Executing, Reviewing, Approval stations in a row, a conveyor lane below. Active station: tinted state color, pulsing ring, progress bar. Completed stations stay tinted with a short result label. Artifacts (plan, diff, verdict) travel along the lane between stations as chips, exactly mirroring files passed through the core. Approval station pulses amber until the human acts, then turns green. Driven by real SSE events, never by timers. Reference: the approved motion preview.
2. **Status badge:** color crossfade and icon swap (150ms) on state change.
3. **Lists and tables:** rows enter with fade + 4px rise; moving between sections ("In progress" to "Needs approval") uses a shared layout animation so the row visibly travels. Removed rows collapse.
4. **Model activity:** streamed Planning/Execution text appears token by token in a mono panel with a blinking caret while active; log lines slide in at the bottom and auto-scroll unless the user scrolled up.
5. **Review verdict:** rule rows resolve in sequence (40ms stagger): spinner, then check (path draws, 200ms) or x (single 4px horizontal nudge). Failed rows move to the top with a layout animation.
6. **Deploy:** environment card shows an indeterminate bar while deploying, then a health dot with a slow heartbeat pulse (2s) while healthy; a failed health check turns the dot red and stops pulsing.
7. **Buttons:** press scale 0.98; async buttons morph into a spinner then a check (150ms each), then return.
8. **Page and dialog transitions:** pages fade + 4px rise (150ms); dialogs and sheets use shadcn defaults; toasts slide from the bottom-right.
9. **Loading:** Skeleton shimmer (1.4s), only while data is genuinely loading.

### Not allowed
Confetti, bouncing icons, parallax, auto-playing decorative backgrounds, animated counters, animation longer than 1s except artifact travel, any animation that repeats when nothing is happening.
