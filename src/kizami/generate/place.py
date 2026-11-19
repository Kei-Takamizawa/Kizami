"""From aligned times to chunk starts, and from units to the three levels.

For each unit, in order:

1. Take its aligned time (the kana model, or the MMS model where the kana
   model is unsure and MMS points at a clearer attack).
2. Snap it to the best vocal attack within ``before`` / ``after`` seconds,
   weighting strength by closeness. A unit with no attack nearby keeps its
   aligned time and is marked uncertain.
3. Move a line's first unit to an earlier strong attack that starts the same
   unbroken sound, and a sibilant to where its hiss begins.
4. Set the chunk start by the owner's convention: the end of the previous
   sound (where the level falls into the last valley before the attack). With
   no valley (legato) it is ``lead`` seconds before the attack, and after a
   silence longer than ``long_gap`` it is where the new sound rises.

Every level uses the same time for the same character.
"""

from __future__ import annotations

import math

import numpy as np

from kizami.generate.audio import SIBILANT, hiss_start

DEFAULTS = {
    # aligned time to attack
    "lag": -0.02,  # added to the aligned time before searching
    "before": 0.07,  # search window before the aligned time (s)
    "after": 0.05,  # ... and after it
    "tau": 0.05,  # closeness weight: strength * exp(-distance / tau)
    "min_gap": 0.06,  # smallest time between two chunk starts (s)
    # chunk start from the loudness curve
    "anchor": "prev-end",  # "prev-end" (owner's convention) or "attack"
    "valley": 0.20,  # look this far back for the last valley (s)
    "peak": 0.25,  # ... and this far before the valley for the previous sound
    "dip": 0.5,  # a valley must fall below this share of the smaller peak
    "silence": 0.05,  # previous sound quieter than this share: treat as silence
    "att": 0.1,  # rise into the attack starts above this share of its peak
    "beta": 0.1,  # previous sound ends where it falls this share towards the valley
    "lead": 0.02,  # legato: start this much before the attack (s)
    "long_gap": 0.30,  # silence longer than this: start at the rise (s)
    # a line starts where its sound starts
    "orphan_min": 3.0,  # strength of an unclaimed attack to move a line start to
    "deep": 0.25,  # ... with a dip before it below this share of its peak
    "cont": 0.35,  # ... and no dip below this share up to the aligned time
    # second aligner
    "mms_dis": 0.15,  # consulted when the two aligners differ by more than this (s)
    "mms_tc": 0.02,  # ... the kana model's confidence is below this
    "mms_tm": 0.02,  # ... the MMS confidence is at least this
    "mms_k": 1.0,  # ... and MMS points at an attack at least this clear
    # level chu
    "m_share": 0.45,  # share of the in-word attacks (strongest first) added to level dai
    "gap": 1.0,  # a unit this long (s) or more after the one before it in its line also starts a chunk
}
LEVELS = ("dai", "chu", "sho")


def attack_start(env, c, v, new_peak, p):
    """Frame where the rise into the attack begins: the last one still near the floor."""
    thr = max(2.0 * env[v], p["att"] * new_peak)
    j = min(c, len(env) - 1)
    while j > v and env[j] > thr:
        j -= 1
    return j


def prev_sound_end(env, tt, t_on, prev_on, p):
    """Chunk start for an attack at ``t_on``, and the rule that decided it.

    Rules: ``prev-end`` (end of the previous sound), ``legato`` (no valley),
    ``silence`` / ``gap`` (start of the rise), ``close`` (too little room to tell).
    """
    c = int(np.searchsorted(tt, t_on + 0.005))
    lo = int(np.searchsorted(tt, max(prev_on, t_on - p["valley"])))
    if c - lo < 4 or c >= len(env):
        return t_on, "close"
    new_peak = env[c : c + 32].max()
    v = lo + int(np.argmin(env[lo:c]))
    a = int(np.searchsorted(tt, max(prev_on, tt[v] - p["peak"])))
    prev_peak = env[a : v + 1].max()
    if prev_peak < p["silence"] * new_peak:
        return float(tt[attack_start(env, c, v, new_peak, p)]), "silence"
    if env[v] > p["dip"] * min(prev_peak, new_peak):
        return t_on - p["lead"], "legato"
    thr = env[v] + p["beta"] * (prev_peak - env[v])
    j = v
    while j > a and env[j] < thr:
        j -= 1
    s = attack_start(env, c, v, new_peak, p)
    if tt[s] - tt[j] > p["long_gap"]:
        return float(tt[s]), "gap"
    return float(tt[j]), "prev-end"


def orphan_onset(onsets, strengths, env, tt, t, earliest, p):
    """An unclaimed strong attack that starts, after a deep dip, the same unbroken sound as t."""
    for k in range(int(np.searchsorted(onsets, earliest)), len(onsets)):
        o = onsets[k]
        if o >= t - p["min_gap"]:
            break
        if strengths[k] < p["orphan_min"]:
            continue
        i0, i1 = int(np.searchsorted(tt, o)), int(np.searchsorted(tt, t))
        peak = env[i0 : i0 + 32].max()
        before = env[int(np.searchsorted(tt, max(earliest, o - p["valley"]))) : i0 + 1].min()
        inside = env[min(i0 + 16, i1) : i1 + 1].min()
        if before <= p["deep"] * peak and inside >= p["cont"] * peak:
            return k
    return None


def fused_rough(atoms, p, onsets, strengths):
    """Aligned time per unit as (first kana, last kana), and how many came from the second aligner.

    The kana model sometimes puts a character a syllable or more away and says
    so with a near-zero confidence. Where the MMS alignment disagrees there,
    is itself not hopeless and points at an attack at least as clear, its time
    is used (shifted by the median offset between the two aligners).
    """
    out = [(a["rough"], a["rough_last"]) for a in atoms]
    both = [i for i, a in enumerate(atoms) if a["rough"] is not None and a.get("mms") is not None]
    if not both or p["mms_dis"] <= 0:
        return out, 0
    off = float(np.median([atoms[i]["rough"] - atoms[i]["mms"] for i in both]))

    def attack_near(r):  # the snapping score of the best attack around an aligned time
        c = r + p["lag"]
        ks = range(int(np.searchsorted(onsets, c - p["before"])), int(np.searchsorted(onsets, c + p["after"], "right")))
        return max((float(strengths[k]) * math.exp(-abs(onsets[k] - c) / p["tau"]) for k in ks), default=0.0)

    n = 0
    for i in both:
        a = atoms[i]
        m = a["mms"] + off
        if abs(a["rough"] - m) > p["mms_dis"] and a["conf"] < p["mms_tc"] and a["mms_conf"] >= p["mms_tm"]:
            if p["mms_k"] > 0 and attack_near(m) < p["mms_k"] * attack_near(a["rough"]):
                continue
            out[i] = (m, m + (a["rough_last"] - a["rough"]))
            n += 1
    return out, n


def place(atoms, onsets, strengths, p, hiss, fps, env=None, tt=None):
    """Set ``time`` (chunk start), ``onset``, ``strength``, ``snapped`` and ``why`` on each unit.

    ``time`` stays None for a unit the aligner could not place (no kana known
    to the model). Without ``env`` the chunk start is the attack itself.
    Returns how many units took their time from the second aligner.
    """
    last_t, last_b, oi, last_kana = -1.0, -1.0, 0, -1.0
    onsets, strengths = np.asarray(onsets), np.asarray(strengths)
    rough, n_mms = fused_rough(atoms, p, onsets, strengths)
    for ai, a in enumerate(atoms):
        a["time"], a["strength"], a["snapped"], a["why"] = None, 0.0, False, ""
        r, r_last = rough[ai]
        if r is None:
            continue
        a["shift"] = r - a["rough"]  # nonzero where the second aligner's time is used
        c = r + p["lag"]
        lo, hi = c - p["before"], c + p["after"]
        while oi < len(onsets) and onsets[oi] < lo - 1.0:
            oi += 1
        best, best_score = None, 0.0
        for k in range(oi, len(onsets)):
            t = onsets[k]
            if t > hi:
                break
            if t < lo or t <= last_t + p["min_gap"]:
                continue
            score = strengths[k] * math.exp(-abs(t - c) / p["tau"])
            if score > best_score:
                best, best_score = k, score
        if best is not None:
            t, a["strength"], a["snapped"] = float(onsets[best]), float(strengths[best]), True
        else:
            t = max(c, last_t + p["min_gap"])  # no attack of its own: estimated from the alignment
        if env is not None and p["orphan_min"] > 0 and a["line_first"]:  # a line starts where the sound starts
            k = orphan_onset(onsets, strengths, env, tt, t, max(last_t + p["min_gap"], last_kana + 0.03), p)
            if k is not None:
                t, a["strength"], a["snapped"], a["why"] = float(onsets[k]), float(strengths[k]), True, "orphan+"
        if a["kana"] and a["kana"][0] in SIBILANT:
            h = hiss_start(hiss, fps, t, last_t + p["min_gap"])
            if h is not None and h < t:
                t = h
        a["onset"] = t
        if env is not None:
            e, why = prev_sound_end(env, tt, t, last_t, p)
            a["why"] += why
            a["time"] = min(t, max(e, last_b + p["min_gap"]))
        else:
            a["time"] = t
        last_t, last_b = t, a["time"]
        last_kana = r_last + p["lag"] if len(a["kana"]) > 1 else -1.0
    return n_mms


# Kana without an attack of their own: they belong to the mora before them.
MORA_ATTACH = set("ゃゅょぁぃぅぇぉゎっー")
MORA_MIN = 0.03  # smallest time between two mora starts inside a unit (s)


def mora_groups(kana):
    """``[(kana text, index of its first kana), ...]``: one entry per mora that can carry an attack."""
    groups = []
    for i, k in enumerate(kana):
        if groups and k in MORA_ATTACH:
            groups[-1][0] += k
        else:
            groups.append([k, i])
    return [(text, i) for text, i in groups]


def place_morae(atoms, onsets, strengths, p, hiss, fps, env=None, tt=None):
    """Set ``morae`` on each placed unit: ``[(kana, start time, has an attack), ...]``,
    and ``mora_strengths``: the strength of each mora's attack (0 for none).

    The first mora starts with the unit. A later mora (the が of 願) is found
    like a unit: its aligned time snaps to the best attack nearby, and its start
    is the end of the sound before it. It stays inside its unit: after the
    previous mora and before the next unit.
    """
    onsets, strengths = np.asarray(onsets), np.asarray(strengths)
    timed = [a for a in atoms if a.get("time") is not None]
    for n, a in enumerate(timed):
        groups = mora_groups(a["kana"])
        a["morae"] = [(groups[0][0], a["time"], a["snapped"])] if groups else []
        a["mora_strengths"] = [a["strength"]] if groups else []
        if len(groups) < 2 or len(a.get("kana_starts", ())) != len(a["kana"]):
            continue
        nxt = timed[n + 1] if n + 1 < len(timed) else None
        next_time = nxt["time"] if nxt else math.inf
        next_onset = nxt["onset"] if nxt else math.inf
        prev_on, prev_t = a["onset"], a["time"]
        for g, (text, i) in enumerate(groups[1:], start=1):
            c = a["kana_starts"][i] + a.get("shift", 0.0) + p["lag"]
            best, best_score = None, 0.0
            k0 = int(np.searchsorted(onsets, c - p["before"]))
            k1 = int(np.searchsorted(onsets, c + p["after"], "right"))
            for k in range(k0, k1):
                t = onsets[k]
                if t <= prev_on + p["min_gap"] or t >= next_onset - p["min_gap"]:
                    continue
                score = strengths[k] * math.exp(-abs(t - c) / p["tau"])
                if score > best_score:
                    best, best_score = k, score
            t = float(onsets[best]) if best is not None else c
            if text[0] in SIBILANT:
                h = hiss_start(hiss, fps, t, prev_on + p["min_gap"])
                if h is not None and h < t:
                    t = h
            start = t
            if env is not None:
                start = min(t, max(prev_sound_end(env, tt, t, prev_on, p)[0], prev_t + p["min_gap"]))
            left = len(groups) - g  # morae still to place, this one included
            lo, hi = prev_t + MORA_MIN, next_time - MORA_MIN * left
            if lo >= hi:  # no room: share what is left evenly
                start = prev_t + (min(next_time, prev_t + 1.0) - prev_t) / (left + 1)
            else:
                start = min(max(start, lo), hi)
            a["morae"].append((text, start, best is not None))
            a["mora_strengths"].append(float(strengths[best]) if best is not None else 0.0)
            prev_on, prev_t = max(t, start), start


def is_word_start(a) -> bool:
    """Level dai keeps a unit that starts a line, or a word that is not a particle or auxiliary."""
    return a["line_first"] or (a["word_start"] and not a["dependent"])


def chu_threshold(atoms, share) -> float:
    """Attack strength above which an in-word attack also starts a chunk at level chu."""
    extra = [a["strength"] for a in atoms if a["snapped"] and not is_word_start(a)]
    return float(np.quantile(extra, 1 - share)) if extra else 0.0


def select(atoms, level, m_thr, gap=None):
    """The units that start a chunk at ``level``.

    At level chu, with ``gap``, a unit that starts ``gap`` seconds or more
    after the unit before it in the same line (a long note or a rest) also
    starts a chunk.
    """
    out, prev = [], None
    for a in atoms:
        if a["time"] is None:
            continue
        if level == "dai":
            keep = is_word_start(a)
        elif level == "chu":
            keep = is_word_start(a) or (a["snapped"] and a["strength"] >= m_thr)
            after_gap = gap is not None and prev is not None and prev["line"] == a["line"]
            keep = keep or (after_gap and a["time"] - prev["time"] >= gap - 1e-9)
        else:
            keep = True  # every unit, with or without an attack of its own
        if keep:
            out.append(a)
        prev = a
    return out


def chunks_for(lines, chosen, atoms=None):
    """Per lyric line, the chunks as (text, start time in s, uncertain, morae).

    A chunk runs from its unit to the next chosen unit of the same line.
    Text before a line's first chosen unit (such as an opening bracket) joins it.
    ``morae`` lists (kana, start time, uncertain) for every mora of the units
    in the chunk when ``atoms`` (all units, with ``morae`` set) is given and the
    chunk has two or more morae; otherwise it is empty.
    """
    index = {id(a): i for i, a in enumerate(atoms or [])}
    out_lines = [[] for _ in lines]
    for i, a in enumerate(chosen):
        nxt = chosen[i + 1] if i + 1 < len(chosen) and chosen[i + 1]["line"] == a["line"] else None
        disp = lines[a["line"]]["display"]
        text = disp[a["offset"] : nxt["offset"] if nxt else None].strip()
        if not out_lines[a["line"]] and a["offset"] > 0:
            text = disp[: a["offset"]].strip() + text
        if not text:
            continue
        morae = []
        if atoms is not None and id(a) in index:
            j = index[id(a)]
            stop = index[id(nxt)] if nxt is not None else len(atoms)
            for u in atoms[j:stop]:
                if u["line"] != a["line"]:
                    break
                morae += [(k, t, not s) for k, t, s in u.get("morae", ())]
            if len(morae) < 2 or any(k == "*" for k, _, _ in morae):
                morae = []
        out_lines[a["line"]].append((text, a["time"], not a["snapped"], morae))
    return out_lines
