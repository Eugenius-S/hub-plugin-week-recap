# Week - an Agents Hub plugin

Once a week - a review of the last week. By default Monday 9:00 in the computer's local time; the day, time and zone are
changed in the Week tab -> "Edit" (or tell the hub: `hub post week-recap "schedule: tue 10:30 Europe/Berlin"`;
`schedule: mon start` means the first start of the hub on that day). The Russian words for the keyword, the days
and "start" are accepted too.

- **Insights** - the main part, first in the report: what you may not know about the hub, what quietly broke or hung
  during the week, what to improve in how you set tasks and give feedback - each with an example from the week.
- **What was done** - by themes (the hub itself, plugins, design, videos, every other project): the tokens of each
  task and each video, first try or with rework, what was dropped. The cost is only tokens, never money.
- **Both computers** - the work of the Mac and of Windows is in one report (the other computer's work comes through the
  hub-sync exchange, `work/<machine>/<week>.jsonl`); the report shows which computers are covered and which sent nothing.
- **For the week** - tasks from Jira (open ones on you), Teams (waiting for your answer) and Granola (agreed, but no task).

How it works: the plugin process reads the hub journal for the week (read only) and builds a digest; the review is
written by the `analyst` agent if there is one, otherwise by the hub. The finished review arrives in Telegram and in the
Week tab; if the review does not come within 6 hours, the numbers alone arrive. Jira goes through the hub's `atlassian`
MCP, if it is connected and granted to whoever writes the review.

Language: the prompts to the analyst and the writer are English and ask for the report in the hub language (Russian hub -
Russian report, any other - English). The report page labels, the notifications and the tab are English by default and
Russian for a Russian hub.

The plugin keeps its files in the hub state folder: `week-recap/<week>/digest.md` (the digest), `work.md` (the week's
work by project, with tokens by computer), `draft.json` (the analyst's draft), `recap.json` (the writer's text), `report.html` (the report).
Build the digest by hand, sending nothing: `python3 main.py --week 2026-W41 --dry` (with `PYTHONPATH` pointing at the hub code).

Install: "Settings" -> "Plugins" -> week-recap. The first review is of last week, right after installing.

## Daily recap

Every morning (9:00 local time of the machine, or at the first start of the hub after 9:00) one message arrives:

- **Yesterday at meetings** - first: what was decided at all of yesterday's meetings (on Monday - since Friday), from Granola.
- **Waiting for you** - what is expected of you at today's and tomorrow's meetings: your open promises from earlier meetings
  of the same series (Granola), the calendar and the chats of Teams, the hub's Teams feed. No such items - no block.
- Everything in progress - on all hubs (this one and the others, if the hub-sync exchange is on), your Jira tasks in the open sprints (the backlog is not
  mentioned) and what hangs with the agents and in the direction backlogs. What has hung longest goes first.

Granola, Teams and Jira are read only (the `granola`, `ms365`, `atlassian` MCP of whoever writes the recap); if one is not
granted, the recap asks the hub for it and says in a line that the source is not in. No answer from the analyst in 45
minutes - the plugin's own text goes, without meetings, Teams and Jira. Time and zone - the post
`hub post week-recap "day: 08:30 Europe/Berlin"`, off - `"day: off"`.

Build the digest by hand, sending nothing: `python3 main.py --day --dry`; send it now: `python3 main.py --day` (again for
the same day - with `--force`). Another hub's open work is `work/<machine>/open.json` in hub-sync.

Code - MIT.

Icon — [3D Fluency](https://icons8.com/icons/3d-fluency) by Icons8.
