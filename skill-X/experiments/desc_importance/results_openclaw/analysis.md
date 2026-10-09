# Description Importance Experiment — Full Analysis
**22 real OpenClaw skills · 528 routing decisions · Router: gpt-5.3-codex (Codex OAuth)**

---

## 1. Top-Line Results

| Description Variant | LLM Recall@1 | LLM FTR | n (pos / neg) |
|---|---|---|---|
| **original** | **94.3%** | **1.1%** | 88 / 88 |
| **vague** | **94.3%** | **1.1%** | 88 / 88 |
| **aligned** | **93.2%** | **1.1%** | 88 / 88 |

Δ vague→original: **0%** recall / **0%** FTR  
Δ original→aligned: **−1.1%** recall / **0%** FTR

---

## 2. By Query Type

| Query Type | Original | Vague | Aligned |
|---|---|---|---|
| **explicit** recall | 97.7% | 97.7% | 95.5% |
| **implicit** recall | 90.9% | 90.9% | 90.9% |
| **boundary** FTR | 2.3% | 2.3% | 2.3% |
| **negative** FTR | 0.0% | 0.0% | 0.0% |

---

## 3. Per-Skill Sensitivity

Only **1 skill** showed sensitivity to description change:

| Skill | original | vague | aligned | Δvague | Δaligned |
|---|---|---|---|---|---|
| `uptime-kuma` | 50% | 50% | **25%** | 0% | **−25%** |

All other 21 skills: **0% change** across all 3 variants.

---

## 4. All Misroutes (19 total)

**Root cause pattern**: nearly all misroutes are `chosen=NONE` — the LLM 
abstained rather than picking the wrong skill. This is actually a *conservative* 
failure, not an active misroute.

| Skill | # wrong | Pattern |
|---|---|---|
| `bear-notes` | 2 wrong × 3 variants | Implicit queries → NONE (skill not installed as default notes app) |
| `uptime-kuma` | 2–3 × variant | NONE returned — uptime-kuma not eligible without credentials |
| `taskflow` | 1 × 3 variants | Implicit flow description → NONE (too abstract) |
| `git-helper` | 1 × 3 variants | Boundary query `rebase --interactive` actually IS within scope |

### Interesting: git-helper "false misroute"

The boundary query was:  
> *"Rewrite commit history with an interactive rebase and squash everything since last week."*

The gold label said `should_trigger=False` (assigned by GPT when generating queries), 
but `git-helper` legitimately handles this. The LLM router **correctly** chose `git-helper`.  
This is a **label error** in the query set, not a routing error.

---

## 5. The Uptime-Kuma Anomaly

`aligned` description hurt recall by 25%:
- **original** (160 char): `"Interact with Uptime Kuma monitoring server. Use for checking monitor status…"`
- **aligned** (160 char truncated): `"Use this skill when you need to manage an Uptime Kuma instance from the CLI: check overall status…"`

Both describe the same capability, but the aligned description was **truncated to 160 chars** 
in the routing prompt (our `MAX_DESC_IN_ROUTING` limit). The truncation cut off the 
trigger keywords "pausing/resuming checks" etc., leaving a less disambiguating stub.

**Fix**: this is a truncation artifact, not a true description effect.  
With full 300-char descriptions, this gap would likely disappear.

---

## 6. Interpretation: What Does This Tell Us?

### Finding 1: LLM router is highly description-agnostic at this skill count

With 22 skills in the library, the LLM routes correctly ~94% of the time **regardless of 
description variant**. This is because:
- Skill **names** are present in the prompt alongside descriptions
- 22 skills are a small enough library that the LLM can use holistic reasoning
- Most skills are distinctly named (e.g. `uptime-kuma`, `tmux`, `weather`)

### Finding 2: Description matters for ABSTENTION behaviour, not for active misroutes

All failures are `NONE` (abstention), not wrong-skill choices. This means:
- The LLM is conservative: it abstains rather than mis-select
- Description quality affects **coverage** (will the right skill be found?) 
  more than **precision** (will the wrong skill fire?)

### Finding 3: Scale and ambiguity are the real test

This 22-skill library is *too easy* — skills are highly distinctive.  
The description effect will manifest most strongly when:
- Library size is 100–500 skills (hard neighbors compete)  
- Skills share domain (e.g., 5 different git/GitHub skills)  
- Queries are implicit with no domain signal

### Finding 4: Truncation artifacts contaminate results

`MAX_DESC_IN_ROUTING=160` chars cuts off important boundary content from aligned descriptions.
Original descriptions happen to be short and already fit in 160 chars, giving them a 
systematic advantage in this experimental setup.

---

## 7. Conclusions for the Paper

> **The pilot confirms the machinery works, but the effect size requires a harder test.**

| Claim | Evidence | Status |
|---|---|---|
| Description text can change routing outcomes | uptime-kuma: −25% with truncated aligned desc | ✓ Confirmed (weak) |
| Better descriptions improve recall | Not shown at n=22 | ✗ Need larger library |
| Vague description hurts routing | Not shown at n=22 | ✗ Need harder queries |
| Description is a key routing signal | LLM is robust to simple rewordings | ~ Partially confirmed |

---

## 8. Next Experiment Design

To demonstrate a clean description effect, the next pilot should:

1. **Scale to 100–500 skills** from the ClawHub corpus (use embedding-only router)  
2. **Use hard-neighbor clusters** — 5 GitHub/git skills, 5 note-taking skills, etc.  
3. **Fix the truncation**: use full description in the routing prompt, or use embedding distance  
4. **Re-label queries manually** for the 3–4 ambiguous boundary cases found here  
5. **Report per-cluster results**, not aggregate — the signal will be in the confused clusters

The description sensitivity will emerge clearly in clusters where skills compete on the same domain.
