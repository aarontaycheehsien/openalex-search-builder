"""Separate-context allocation and one-time external validation.

The reviewer workspace must be inaccessible to the strategy-building agent.
This module does not implement filesystem permissions or launch another agent.
"""
from pathlib import Path

from .api import Error, now, work_id
from .records import discovered_ids, wilson
from .store import Run, digest


def independent_workspace(run, development):
    private = run.root.resolve()
    public = Path(development).resolve()
    if private == public or private in public.parents or public in private.parents:
        raise Error("Use separate, non-nested reviewer and development workspaces.")
    other = Run(public)
    if protocol_key(run.get("protocol")) != protocol_key(other.get("protocol")):
        raise Error("Reviewer and development workspaces must use the same question and eligibility.")
    return other


def protocol_key(protocol):
    return {k: protocol[k] for k in ("question", "eligibility")}


def seen_ids(run):
    return set(run.get("records")) | discovered_ids(run)


def split_holdouts(run, development, seed=42):
    """Split screened citation candidates, revealing only development IDs to the builder."""
    other = independent_workspace(run, development)
    if (run.root / "blind-holdouts.json").exists():
        raise Error("Allocation is already sealed. Do not resplit to select a favourable validation set.")
    if not (run.root / "screening.json").exists():
        raise Error("Screen citation-discovered candidates in the separate reviewer workspace first.")
    records = run.get("records")
    decisions = run.get("screening").values()
    included = set()
    conflicts = set()
    for event in decisions:
        wid = event["work_id"]
        if event["decision"] != "include":
            conflicts.add(wid)
            continue
        batch = run.get("discovery/" + event["discovery"])
        if batch["route"] == "neighbors" and wid in records and records[wid]["role"] in ("relevant", "validation") and records[wid]["work"]:
            included.add(wid)
    # Exclude candidates already seen by the builder, and conflicting screening decisions.
    eligible = sorted(included - conflicts - seen_ids(other),
                      key=lambda wid: (digest([seed, wid]), wid))
    if len(eligible) < 2:
        raise Error("At least two new, eligible citation-discovered works are required for a split.")
    n = len(eligible) // 2
    held = sorted(eligible[:n])
    dev = sorted(eligible[n:])
    sealed = {"allocated_at": now(), "seed": seed, "protocol": protocol_key(run.get("protocol")),
              "development_workspace": str(other.root),
              "existing_reports": sorted(p.name for p in (other.root / "reports").glob("*") if p.is_dir()),
              "development_ids": dev, "validation_ids": held,
              "selection": "separately screened citation candidates; deterministic half split"}
    run.put("blind-holdouts", sealed)
    # This is the only file/output intended to be shared with the builder.
    return {"development_ids": dev, "validation_count": len(held),
            "note": "Share only this output with the development agent. Keep the reviewer workspace private."}


def score_holdouts(run, api, report_path):
    """Score exactly one delivered query, with no held-out metadata in the builder workspace."""
    if (run.root / "blind-score.json").exists():
        raise Error("This allocation has already been scored and exposed. Use a new independent set for another evaluation.")
    sealed = run.get("blind-holdouts")
    other = independent_workspace(run, sealed["development_workspace"])
    dest = Path(report_path).resolve()
    if dest.parent != (other.root / "reports").resolve():
        raise Error("Choose a delivered report from the allocated development workspace.")
    archive = other.get("reports/" + dest.name + "/validation")
    if (dest.name in sealed["existing_reports"] or archive["evaluation_id"] != dest.name
            or archive["evaluation"]["at"] < sealed["allocated_at"]):
        raise Error("Score a report delivered after allocation.")
    if protocol_key(archive["scope"]["protocol"]) != sealed["protocol"]:
        raise Error("Report eligibility differs from the sealed allocation.")
    query = other.get("reports/" + dest.name + "/final-query")
    final = archive["evaluation"]["final"]
    if query != final["oqo"]:
        raise Error("The delivered query does not match the validation archive.")
    seen = seen_ids(other) | set(archive["records"])
    original = set(sealed["validation_ids"])
    contaminated = set()
    unresolved = set()
    resolved = set()
    for wid in sorted(original):
        if wid in seen:
            contaminated.add(wid)
            continue
        _, work = api.resolve(wid)
        if work is None:
            unresolved.add(wid)
            continue
        canonical = work_id(work)
        if canonical in seen:
            contaminated.add(wid)
        else:
            resolved.add(canonical)
    hits = api.members(query, resolved, include_pulled=archive["evaluation"]["pulled"]["include_pulled"]) if resolved else set()
    if not hits.issubset(resolved):
        raise Error("Holdout intersection contains unexpected IDs.")
    public = {"report_id": dest.name, "scored_at": now(), "allocated": len(original),
              "contaminated_excluded": len(contaminated), "unresolved": len(unresolved),
              "resolved_unexposed": len(resolved), "retrieved": len(hits),
              "relative_recall": len(hits) / len(resolved) if resolved else None,
              "relative_recall_ci95": wilson(len(hits), len(resolved)),
              "delivered_screening_count": final["count"],
              "note": "One-time external validation on the current index. Independence requires separate agent context and filesystem access. Candidate selection is citation-based, not a random sample of all eligible studies."}
    run.put("blind-score", {"summary": public, "query": query, "contaminated": sorted(contaminated),
                           "unresolved": sorted(unresolved), "resolved": sorted(resolved), "hits": sorted(hits)})
    return public
