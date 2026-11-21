---
title: What konsol posts, what you post
audience: Everyone
section: Concepts
order: 130
---
Some consolidation entries konsol computes from your trial balances and declarations. Others konsol does not compute. You post those yourself as a **Consolidation Journal**. This page lists both, so you know what to expect in the consolidation worksheet.

## Computed by konsol

Each entry below lands in its own layer of the consolidation. The layer name is the `adjustment_type` the warehouse writes. The column is the one in the consolidation waterfall.

| Entry | Layer | Waterfall column |
|---|---|---|
| Entity trial balance, translated | `entity` | Entity |
| Intercompany eliminations, in full (see Intercompany elimination) | `ic_elimination` | IC elimination |
| Currency translation adjustment (CTA) | `cta` | CTA |
| Consolidation Journals you post (topside and reclassification), and their auto-reversal | `topside`, `reclassification`, `auto_reversal` | Topside |
| Equity method: share of an associate's profit | `equity_method` | Equity method |
| Acquisition journal, with goodwill, from a submitted Business Combination | `acquisition` | Acquisition and disposal |
| Goodwill amortisation, only when the group declares the Amortise policy | `goodwill_amortisation` | Acquisition and disposal |
| Disposal journal, for a full disposal, from a submitted Business Disposal | `disposal` | Acquisition and disposal |
| NCI attribution: the minority's share of profit and CTA of a subsidiary | `nci_attribution` | NCI attribution |
| Year-end journal close: each fiscal year's P&L of the journal layers moved into retained earnings, in the Closing period | `journal_close` | Journal close |

An approved Consolidation Journal that names a reversal period gets one `auto_reversal` row in that period, with debit and credit swapped.

Goodwill amortisation posts nothing when the policy is Impairment only.

The journal close covers every layer except `entity` (each entity's own year-end close) and `ic_elimination` (which reverses its own P&L legs). It posts only when the chart flags exactly one retained-earnings account.

## Posted by you

konsol does not compute these. If one applies to your group, post it as a Consolidation Journal.

| Entry | Gap |
|---|---|
| Opening consolidation for subsidiaries held before go-live | No automatic opening. Post the opening balances yourself. |
| Goodwill impairment | [konsolidat#382](https://github.com/grynn-in/konsolidat/issues/382). No field, test or journal. |
| Deferred tax on consolidation adjustments | [konsolidat#383](https://github.com/grynn-in/konsolidat/issues/383). Not computed on fair-value adjustments or on unrealised profit eliminations. |
| Step acquisition remeasurement of the previously held interest | [konsolidat#384](https://github.com/grynn-in/konsolidat/issues/384). Not remeasured at the date control is gained. |
| Intragroup dividends | [konsolidat#385](https://github.com/grynn-in/konsolidat/issues/385). Not eliminated, so group profit includes dividends from subsidiaries. |
| Associate dividends, share of OCI and unrealised profit | [konsolidat#386](https://github.com/grynn-in/konsolidat/issues/386). The equity method posts the share of profit only. |
| Partial disposals and retained interests | [konsolidat#310](https://github.com/grynn-in/konsolidat/issues/310). The disposal journal posts nothing, and refuses by name. |

## Related gaps

These are open and have no fix scheduled here.

- [konsolidat#318](https://github.com/grynn-in/konsolidat/issues/318): an ownership step on a controlled subsidiary does not rebalance NCI.
- [konsolidat#311](https://github.com/grynn-in/konsolidat/issues/311): NCI attribution does not cover the minority's share of equity movements or of prorated pre-acquisition profit.
- [konsolidat#348](https://github.com/grynn-in/konsolidat/issues/348): a Business Combination for an entity the group already held (a step acquisition) is not pro-rated.
- [konsolidat#387](https://github.com/grynn-in/konsolidat/issues/387): to verify whether goodwill and fair-value adjustments of a foreign subsidiary are retranslated at the closing rate.
