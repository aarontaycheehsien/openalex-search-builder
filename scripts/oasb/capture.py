"""Capture–recapture estimate of how many relevant works exist, from two discovery routes.

During development the agent finds eligible works by text routes (`sample`, `sample --added`,
`neighbors --direction related`, which is text similarity) and by citation routes (`neighbors`
citing or references). Treating the two routes as two "captures", the overlap between them gives
Chapman's (1951) bias-corrected Lincoln–Petersen estimate of the total, with the variance usually
attributed to Seber (1970). Spoor et al. (1996) applied capture–recapture to the completeness of
literature searches, and Kastner et al. (2009) used it as a stopping rule.

It estimates works this workflow judges eligible, not a review's included set, and the two routes are
not independent, so the bias can go either way: shared vocabulary and seeds make it too small, routes
that reach different parts of the literature (citations from older seeds, say) make it too large.
Citation links are incomplete (about 82% of references to works in OpenAlex are linked). In ten blind
runs (2026-11-26) the overlap between routes was 0-5 works, the estimates were 31-125 works with wide
intervals, and the agents' eligible works overlapped the reviews' included studies by 0-63% (median 6%),
so the estimate could not be checked against gold (a review's included set can be only part of the eligible literature, so low overlap need not mean over-inclusion). Use it as a signal of how complete discovery is by
the workflow's own eligibility judgements, not as a count of a review's studies.

  Chapman, D. G. (1951). Some properties of the hypergeometric distribution with applications to
    zoological sample censuses. University of California Publications in Statistics, 1, 131-160.
  Seber, G. A. F. (1970). The effects of trap response on tag recapture estimates. Biometrics, 26,
    13-22. https://doi.org/10.2307/2529040
  Spoor, P., Airey, M., Bennett, C., Greensill, J., & Williams, R. (1996). Use of the capture-recapture
    technique to evaluate the completeness of systematic literature searches. BMJ, 313, 342-343.
    https://doi.org/10.1136/bmj.313.7053.342
  Kastner, M., Straus, S. E., McKibbon, K. A., & Goldsmith, C. H. (2009). The capture-mark-recapture
    technique can be used as a stopping rule when searching in systematic reviews. Journal of Clinical
    Epidemiology, 62, 149-157. https://doi.org/10.1016/j.jclinepi.2008.06.001
"""
import json
import math

TEXT, CITATION = "text", "citation"
Z = 1.959964
CAVEAT = ("It counts works this workflow judges eligible, not a review's included studies, and it is rough: the two "
          "routes are not independent, so the bias can go either way, and citation links are incomplete (about 82% of "
          "references to works in OpenAlex are linked). Read low completeness as a reason to keep discovering, not "
          "as a recall figure.")


def route_of(batch):
    """Which capture a discovery batch belongs to: text (keyword samples, related works) or citation."""
    kind = batch.get("route_kind")
    if kind in ("text", "text-similarity"):
        return TEXT
    if kind == "citation":
        return CITATION
    if batch.get("route") == "neighbors":   # batches saved before route_kind existed
        return TEXT if batch.get("direction") == "related" else CITATION
    return TEXT


def chapman(n1, n2, m):
    """Chapman's estimate of the total and a 95% interval (normal approximation, floored at the observed count)."""
    estimate = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    variance = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    observed = n1 + n2 - m
    half = Z * math.sqrt(variance)
    return estimate, [max(observed, estimate - half), estimate + half]


def capture_recapture(run):
    """Estimate from the workspace's own discovery batches and screening decisions; no API calls."""
    folder = run.root / "discovery"
    screening = run.get("screening") if (run.root / "screening.json").is_file() else {}
    eligible = {e["work_id"] for e in screening.values() if e["decision"] == "include"}
    seen = {TEXT: set(), CITATION: set()}
    batches = {TEXT: 0, CITATION: 0}
    legacy = 0
    for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        batch = json.loads(p.read_text(encoding="utf-8"))
        route = route_of(batch)
        batches[route] += 1
        if "protected_hits" not in batch:
            legacy += 1
        seen[route] |= {r["id"] for r in batch.get("rows", [])} | set(batch.get("protected_hits", []))
    text, citation = eligible & seen[TEXT], eligible & seen[CITATION]
    n1, n2, m = len(text), len(citation), len(text & citation)
    out = {"eligible_found": len(eligible), "text_route": n1, "citation_route": n2, "both_routes": m,
           "batches": batches, "method": "Chapman (1951) estimator, Seber (1970) variance", "caveat": CAVEAT}
    if legacy:
        out["note"] = (f"{legacy} discovery batch(es) predate the recording of hidden validation works, so a validation "
                       "work found again by them is not counted as a recapture (the estimate may be too high).")
    if not n1 or not n2:
        out["estimate"] = None
        out["message"] = ("No estimate: it needs eligible works found by both a text route (sample, related) and a "
                          "citation route (neighbors citing or references).")
        return out
    estimate, ci = chapman(n1, n2, m)
    observed = n1 + n2 - m
    out.update({"observed": observed, "estimate": round(estimate, 1), "ci95": [round(ci[0], 1), round(ci[1], 1)],
                "discovery_completeness": round(observed / estimate, 3) if estimate else None})
    if m == 0:
        out["message"] = ("No work was found by both routes, so the estimate is unstable. Keep discovering by both routes "
                          "before relying on it.")
    elif m < 5:
        out["message"] = f"Only {m} work(s) found by both routes: the interval is wide."
    return out


def text(c):
    if c.get("estimate") is None:
        return c["message"]
    line = (f"{c['observed']} eligible works were found during discovery: {c['text_route']} by text routes, "
            f"{c['citation_route']} by citation routes, {c['both_routes']} by both. Capture–recapture estimates about "
            f"{c['estimate']:.0f} relevant works (95% CI {c['ci95'][0]:.0f}–{c['ci95'][1]:.0f}), so discovery found about "
            f"{100 * c['discovery_completeness']:.0f}% of them. {c['caveat']}")
    return line + (" " + c["message"] if c.get("message") else "") + (" " + c["note"] if c.get("note") else "")
