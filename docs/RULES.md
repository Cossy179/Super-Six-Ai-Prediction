# Super 6 rules as encoded

Checked on **5 November 2026** against the operator's 2026/27
[Terms & Conditions](https://super6.skysports.com/terms) and [FAQ](https://super6.skysports.com/faq).
The settings live in `configs/competition_rules.yaml` (`rules_version: sky-terms-2026-27`).
Re-check whenever a new season's terms appear, then bump `rules_version`.

| Topic | Operator wording (quoted) | Setting |
|---|---|---|
| Points | "Correct result = 2 points. Correct score = 5 points." "A maximum of 5 points can be achieved for each fixture within a round." | `5 / 2 / 0`; a correct score is 5 in total, not 5 + 2 |
| Settlement | "All predictions are settled at Full-time (including stoppage time). Extra time and penalty shoot-outs do not count." | `settlement_period: full_time` |
| Deadline | "Each Round closes at the kick off time of the first fixture of each round." | Round files must not set a deadline after the first kick-off; an earlier one is flagged |
| Golden Goal | "make a prediction of the time of the earliest goal to be scored in all matches in a round" | see the interpretation below |
| Minutes | "0 - 59 seconds = 1st minute; 1m00s - 1m59s = 2nd minute … if a player selects minute 6 … 5m00s - 5m59s." | `golden_goal_minute_indexing: one_based` |
| Stoppage time | "Any goals scored in injury time will be recorded as forty-five minutes for the first half and ninety minutes for the second half of the fixture." | `golden_goal_stoppage_time: capped_at_period_end` |
| Closest wins | "closest to the actual time of the first goal across any of the six games … times before and after the actual time of the first goal will be considered equally." | `golden_goal_tiebreak: closest_minute`, so the pick is the median minute |
| No goals | "If there are no goals in any of the fixtures, then the correct answer for that Round will be deemed to be 0." | `golden_goal_no_goal_minute: 0` |
| Void fixtures | "If any Super 6 fixture is postponed, abandoned or not completed (where less than 90mins is played) the fixture will be considered void." | Void fixtures score nothing at settlement |
| Ties on points | Leaderboards: more correct scores first, then the closest Golden Goal (FAQ). Jackpot and Top Scorer prizes: the closest Golden Goal (Terms). | Not used by the optimiser |
| Data source | "The fixture scores and time of first goal … are published by Stats Perform group ('Opta')." | — |

## Interpretation: which goal is "the earliest" when kick-offs differ

The terms do not say how to order goals from games that kick off at different times. The
engine uses **the lowest recorded minute in any of the six games**
(`golden_goal_first_goal: earliest_match_minute`), for two reasons:

- Stoppage-time goals are all recorded as 45 or 90, so the published goal times cannot
  put goals in real-time order. Comparing recorded minutes is the only rule that works
  on the data the operator says it uses.
- The terms describe a goal's "time" only as its recorded match minute.

When a round has staggered kick-offs and the two readings give different picks, the CLI
warns `GOLDEN_GOAL_STAGGERED_KICKOFFS` and shows the minute under the real-time reading.
If the operator confirms the other reading, set
`golden_goal_first_goal: first_in_real_time` and bump `rules_version`.
