# Getting Started Help And First-Run Prompt — Design Spec

## Overview

Expand the existing `Getting Started` dialog into a practical, reusable guide for intermediate Mewgenics players who understand breeding in the game but do not yet understand how to use this app well.

The guide should serve two jobs:

1. Be available at any time from `Help > Getting Started`
2. Be discoverable on startup through a lightweight first-run prompt

This is not a live UI spotlight tutorial. It is a multi-page walkthrough that explains what each major function is for, when to use it, and what kind of answer it gives the player.

The guide should reflect the app's real value proposition:

- it puts all your cat information in one place
- it helps you inspect and organize your population
- it helps you identify weak cats to cull or donate
- it helps you understand pair recommendations and longer-term breeding plans

The copy should be practical and plainspoken rather than polished marketing language.

## Goals

- Give new-to-the-tool users a clear starting point without overwhelming them
- Explain the main workflow of the app from roster inspection through planning
- Explain major views in terms of player decisions, not just UI controls
- Keep `Getting Started` useful as a permanent help reference
- Add a startup prompt that asks whether to open the guide, skip once, or always skip
- Preserve the existing `What's New` flow for release notes

## Non-Goals

- No live guided tour that highlights widgets or forces navigation through the main window
- No rewrite of `About` or `What's New` into documentation hubs
- No large-scale documentation system outside the existing dialog flow
- No dependency on external network content inside the app beyond clickable links

## Existing Context

The app already has:

- `Help > Getting Started`
- `Help > What's New`
- `Help > About`
- an existing `OnboardingDialog` implemented as a `QStackedWidget` with `Back`, `Next`, and `Finish`
- startup logic that decides whether to show onboarding or `What's New` based on `last_seen_version`

That existing structure should be reused rather than replaced.

## User Experience Summary

### First-Run / Startup Experience

When a user launches the app and onboarding prompting is still enabled, the app should show a small modal prompt before the full guide.

The prompt should not try to teach the app. Its only job is to route the user into the expanded guide or let them dismiss it.

Suggested prompt content:

- Title: `New to Mewgenics Breeding Manager?`
- Body: short copy explaining that the app has grown a lot and the guide walks through the main workflow and major tools
- Buttons:
  - `Open Getting Started`
  - `Skip This Time`
  - `Always Skip`

Behavior:

- `Open Getting Started` opens the full guide
- `Skip This Time` closes the prompt and asks again on a later launch
- `Always Skip` stores a persistent preference and suppresses future auto-prompts
- `Help > Getting Started` always remains available regardless of the saved preference

### Help Menu Experience

`Help > Getting Started` should always open the full guide directly.

This should not show the startup prompt first. The prompt is only for automatic startup behavior.

### Guide Experience

The guide remains a modal multi-page dialog with:

- one page title
- one body area using `QTextBrowser`
- `Back`, `Next`, and `Finish` buttons
- a `Page X of Y` label

The dialog remains reusable from Help and from startup.

## Information Architecture

The expanded guide should use nine pages.

### 1. Start Here

Purpose:
Explain what the app is for in plain language.

Required content:

- The app puts all the information about your cats in one place so you can make better decisions
- The main use case is understanding your population before deciding what to breed, keep, or cull
- The common long-term goal is preserving strong stats and strong mutations while removing low-value cats and weak lines
- A beginner-friendly LLM suggestion appears near the top:
  - export cats with `File > Export Cats`
  - give an LLM the exported CSV plus the [Breeding wiki page](https://mewgenics.wiki.gg/wiki/Breeding)
  - ask what to do next

Tone:
Very clear and grounded. This page should reduce “where do I even start?” anxiety.

### 2. Your Main Workflow

Purpose:
Explain the normal loop for using the app.

Required content:

- Load your save
- Inspect the roster
- Identify weak cats and obvious cuts
- Compare promising pair options
- Use planning tools when you want a longer-term strategy
- Export and ask for help if you need another layer of interpretation

Key message:
The app works best as a funnel. Start broad with the full roster, then narrow into scoring, pair analysis, and planning.

### 3. Roster: Your Home Base

Purpose:
Explain how to use the roster as the default starting point.

Required content:

- Sort and filter to understand your full population
- Click cats to inspect details
- Use the roster to compare stats, mutations, disorders, lineage, and other context
- Use tags and quick actions to stay organized

Key message:
If you are confused, start in the roster.

### 4. Find Weak Cats Fast

Purpose:
Teach the fastest path to culling and triage.

Required content:

- `Donation Candidates` is for quick triage and finding the worst offenders
- `Simple Scoring` is for ranking cats according to your own priorities
- This is the best place to start if the real goal is to identify shitty cats, weak lines, or obvious cuts

Page structure:

- short intro paragraph
- one subsection for `Donation Candidates`
- one subsection for `Simple Scoring`
- one short “use this when...” summary for each

### 5. Understand Pair Suggestions

Purpose:
Explain what pair recommendation tools are doing and why their output can be non-obvious.

Required content:

- Pair views are not only matching the two most obviously strong cats
- The app is searching across many possible combinations and surfacing promising pairs based on tradeoffs
- A pair may be recommended because it supports a better long-term direction, preserves rare strengths, avoids a worse risk, or complements weaknesses well
- If the app spits out several top pairs, that is a signal about strategy rather than a command

This page should also include example LLM prompts such as:

- `Why is the tool recommending these 4 pairs?`
- `What is it trying to optimize for?`
- `What tradeoffs is it making between these pair options?`

### 6. Detailed Scoring

Purpose:
Explain when and why to use the more strategic scoring tools.

Required content:

- `Detailed Scoring` is a more strategic ranking tool than `Simple Scoring`
- It helps when the roster is large enough that quick eyeballing stops working
- Profiles and weights let the player ask different strategic questions
- Users do not need to master this immediately

Key message:
This is the place to come when you want more nuance, not the first tool every user must learn.

### 7. Planning Tools

Purpose:
Explain longer-horizon tools like room and planner views.

Required content:

- Planning views matter once the player already knows which cats are worth investing in
- These tools help compare breeding directions, not just one-off cats
- The value here is project-level planning rather than quick inspection

This page should explain that the app can help the player think in generations, not just individual pair quality.

### 8. Family Tree And Context

Purpose:
Explain why the app exposes family and context-heavy views.

Required content:

- Good breeding decisions are not just about one cat’s current score
- Family history and lineage matter
- Use these views to understand how traits and risks move through a line

Key message:
The app keeps context because breeding decisions are better when you can see the line behind the cat.

### 9. Export And Ask For Help

Purpose:
Close the guide with a practical “what next?” page.

Required content:

- Remind the user about `File > Export Cats`
- Explain that exporting lets the player use outside help without retyping everything
- Include example prompts:
  - `Help me decide who to cull first`
  - `Why are these pairs being recommended`
  - `What should I do next with this roster`
  - `Which lines are worth keeping and which should I stop investing in`

This page should reinforce the wiki + CSV + LLM workflow from page 1.

## Copywriting Guidelines

- Write for intermediate players who know Mewgenics breeding concepts but do not know this tool
- Keep the tone practical, direct, and plainspoken
- Avoid sounding like release notes or marketing copy
- Prefer “what this feature is for” over “here is every control on this screen”
- Use blunt phrasing like “find weak cats fast” or “worst offenders” where it improves clarity

## UI Design Details

### Keep The Existing Dialog Pattern

Reuse the existing `OnboardingDialog` pattern:

- `QDialog`
- `QStackedWidget`
- one page per concept
- `Back`, `Next`, `Finish`
- `Page X of Y`

This avoids introducing a second documentation surface.

### Suggested Dialog Changes

- Keep the window title as `Getting Started`
- Change the top heading from `Welcome to Mewgenics Breeding Manager` to something more reusable, such as `Getting Started With Mewgenics Breeding Manager`
- Keep the existing dark styling unless a separate UI cleanup is needed later
- Allow links in page bodies

### Readability Constraints

Each page should be:

- one short intro paragraph
- 3 to 6 bullets or short subsections
- optionally one short “try this” or “use this when...” block

Pages can scroll, but the design target should be “readable in one sitting,” not mini-manuals.

## Startup Prompt Design

### New Dialog

Add a lightweight startup prompt dialog separate from the full guide.

Responsibilities:

- ask whether the user wants help getting started
- optionally suppress future startup prompting
- launch the full guide when requested

This prompt should not replace the full guide. It only decides whether to open it automatically.

### Persisted Preference

Store a dedicated onboarding-prompt preference in app config.

Required shape:

- `show_getting_started_prompt: true/false`

This key must be separate from version tracking and should default to `true` when missing.

### Separation From Release Notes

Do not overload `last_seen_version` for onboarding prompting.

Required behavior:

- `What's New` remains version-driven
- onboarding prompt remains preference-driven

This avoids conflicts such as:

- a returning user who has skipped onboarding forever but should still see a new release note
- a first-time user who should see onboarding help without teaching the app through the release-notes dialog

## MainWindow Behavior

Startup flow should become:

1. Check whether startup dialogs have already been shown
2. If the user has not seen the current version’s release notes, preserve the existing `What's New` behavior
3. Independently decide whether to show the onboarding/startup prompt
4. If the user chooses `Open Getting Started`, open the full guide

Ordering requirement:

- First-time users should see the onboarding prompt rather than silently dropping into the main window
- Returning users with a new version should still get `What's New`
- If both are eligible, the onboarding prompt should not permanently suppress `What's New`, and vice versa

Recommended handling:

- show `What's New` when needed
- then show onboarding prompt if onboarding prompting is enabled and the user still qualifies for it

This keeps release messaging and evergreen help separate.

## Implementation Boundaries

Touched areas:

- `src/mewgenics/dialogs.py`
  - expand the existing `OnboardingDialog`
  - add the lightweight startup prompt dialog
- `src/mewgenics/main_window.py`
  - update startup flow
  - keep `Help > Getting Started` opening the full guide directly
- `src/mewgenics/utils/config.py`
  - add persistent config helpers for onboarding prompt preference

No other architectural changes are required for this feature.

## Edge Cases

- Users who clicked `Always Skip` must still be able to open the guide from `Help`
- Users upgrading versions should still get `What's New`
- Users with missing or corrupted config should default to seeing the startup prompt
- If the guide is opened from Help, it should never write skip preferences just because it was viewed

## Testing Strategy

Manual verification is sufficient for this feature.

Required checks:

- Fresh config shows the startup prompt
- `Open Getting Started` opens the full guide
- `Skip This Time` closes the prompt and shows it again next launch
- `Always Skip` suppresses the prompt on later launches
- `Help > Getting Started` still works after `Always Skip`
- `What's New` still appears on version change
- The new guide pages render correctly and links open externally
- Page navigation works correctly from first page to last page

## Open Decisions Resolved

- Use the simpler multi-page guide, not a live step-by-step UI spotlight tutorial
- Support both startup discovery and persistent Help access
- Target intermediate players who know the game but not the tool
- Keep the beginner-friendly LLM suggestion near the top of the guide
- Add feature explanations oriented around player decisions rather than raw control descriptions
