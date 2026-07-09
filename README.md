# UN Vacancy Monitor

A personal dashboard tracking private-sector partnership, fundraising and resource-mobilization vacancies at **UNHCR**, **UNICEF** and **WFP** — filtered for P3/P4/P5 roles.

It is a static site (GitHub Pages) plus a scheduled GitHub Action that refreshes `data/jobs.json` daily.

## Setup (one time, ~5 minutes)

1. **Create a repo and push this folder**
   ```bash
   cd un-vacancy-monitor
   git init && git add -A && git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/un-vacancy-monitor.git
   git push -u origin main
   ```

2. **Enable GitHub Pages**
   Repo → Settings → Pages → Source: *Deploy from a branch* → Branch: `main`, folder `/ (root)` → Save.
   Your dashboard will be at `https://<your-username>.github.io/un-vacancy-monitor/`.

3. **Run the first data refresh**
   Repo → Actions → *Update vacancies* → *Run workflow*.
   This replaces the sample data with live postings. It then re-runs automatically every day at 06:00 UTC.

## How it works

- `index.html` — the dashboard. Reads `data/jobs.json`, no backend needed.
- `scripts/fetch_jobs.py` — pulls postings from:
  - **UNICEF** via the public SmartRecruiters API (reliable)
  - **UNHCR** via the Workday careers JSON endpoint (reliable, dates are approximate)
  - **WFP** via a best-effort parse of their SuccessFactors career site (**most fragile** — if it breaks, the workflow keeps the last good WFP data and flags the issue in the page footer)
- `.github/workflows/update-jobs.yml` — daily scheduled fetch + commit.

If any single source fails, previous data for that agency is kept, so the page never goes blank. Source problems appear in the footer ("source issues: …").

## Customizing

Open `scripts/fetch_jobs.py` and edit the CONFIG block at the top:

- `KEYWORDS` — terms that make a posting relevant (matched against title/summary)
- `LEVELS` — grades to keep (`["P4", "P3", "P5"]` by default; set to `[]` to keep all)

Saved roles (the ☆ button) are stored in your browser's localStorage — they persist on your device but don't sync between devices.

## Possible next steps

- **Email alerts**: add a step to the workflow that diffs `jobs.json` against the previous commit and emails new matches (e.g. with `dawidd6/action-send-mail` and a Gmail app password stored as a repo secret).
- **WFP hardening**: if the WFP parser breaks often, swap it for a scrape of a stable aggregator or their RSS feed if one becomes available.
