# Week - an Agents Hub plugin

Once a week - a review of the last week. By default Monday 9:00 in the computer's local time; the day, time and zone are
changed in the Week tab -> "Edit" (or tell the hub: `hub post week-recap "schedule: tue 10:30 Europe/Berlin"`;
`schedule: mon start` means the first start of the hub on that day). The Russian words for the keyword, the days
and "start" are accepted too.

- **Tasks** - your human tasks, not the agents' steps: what was done, what is in progress, what was dropped, first try or
  with rework; Jira tasks closed during the week.
- **Efficiency of the pair** - the share of tasks done on the first try, how much of your time went (messages, forms,
  rework), how long you waited for a result.
- **Recommendations** - which agents or subagents to add, what to install, in what format to give instructions.

How it works: the plugin process reads the hub journal for the week (read only) and builds a digest; the review is
written by the `analyst` agent if there is one, otherwise by the hub. The finished review arrives in Telegram and in the
Week tab; if the review does not come within 6 hours, the numbers alone arrive. Jira goes through the hub's `atlassian`
MCP, if it is connected and granted to whoever writes the review.

Language: the prompts to the analyst and the writer are English and ask for the report in the hub language (Russian hub -
Russian report, any other - English). The report page labels, the notifications and the tab are English by default and
Russian for a Russian hub.

The plugin keeps its files in the hub state folder: `week-recap/<week>/digest.md` (the digest), `work.md` (the week's
work by project), `draft.json` (the analyst's draft), `recap.json` (the writer's text), `report.html` (the report).
Build the digest by hand, sending nothing: `python3 main.py --week 2026-W41 --dry` (with `PYTHONPATH` pointing at the hub code).

Install: "Settings" -> "Plugins" -> week-recap. The first review is of last week, right after installing.

Code - MIT.

Icon — [3D Fluency](https://icons8.com/icons/3d-fluency) by Icons8.
