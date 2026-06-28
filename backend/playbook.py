"""
Standard positions for common contract clauses.
Update this dict to reflect actual legal positions — the pipeline reads it verbatim.
"""

STANDARD_POSITIONS: dict[str, str] = {
    "Confidentiality term": (
        "Maximum 3 years post-disclosure. Perpetual confidentiality obligations are not acceptable. "
        "Standard carve-outs apply: information already public, independently developed, received from a third party."
    ),
    "Liability cap": (
        "Aggregate liability must be capped at the total fees paid in the 12 months preceding the claim. "
        "Unlimited liability or higher caps are non-standard and require escalation."
    ),
    "IP ownership": (
        "Each party retains ownership of its pre-existing IP. "
        "No assignment of background IP. Work-for-hire clauses that claim ownership of the company's deliverables are not acceptable."
    ),
    "Governing law": (
        "England and Wales. Any other jurisdiction requires legal sign-off."
    ),
    "Dispute resolution": (
        "Courts of England and Wales. Mandatory arbitration clauses must be reviewed before acceptance."
    ),
    "Non-solicitation": (
        "Maximum 12 months post-termination. No broader non-compete clauses. "
        "Mutual non-solicitation is acceptable; one-sided clauses targeting only the company are not."
    ),
    "Data processing": (
        "If personal data is processed, a GDPR-compliant Data Processing Agreement (DPA) must be in place before execution. "
        "Contracts that involve personal data without a DPA are blocked from auto-approval."
    ),
    "Auto-renewal": (
        "Maximum 12-month automatic renewal term. "
        "Notice period for opting out of renewal must be no more than 90 days before the renewal date."
    ),
    "Assignment": (
        "Neither party should be able to assign the agreement without the other's written consent, "
        "except in the case of a merger or acquisition of the assigning party."
    ),
    "Indemnification": (
        "Mutual indemnification for IP infringement and gross negligence is standard. "
        "Broad indemnification for any third-party claim or consequential loss is not acceptable."
    ),
    "Termination for convenience": (
        "Either party should be able to terminate with 30 days written notice. "
        "Lock-in clauses requiring longer notice or imposing penalties for termination are non-standard."
    ),
    "Warranties": (
        "Standard warranties: each party has authority to enter the agreement, no conflicting obligations. "
        "Broad fitness-for-purpose or outcome-based warranties on the company's products are not acceptable."
    ),
}

PLAYBOOK_SUMMARY = "\n\n".join(
    f"**{clause}**: {position}" for clause, position in STANDARD_POSITIONS.items()
)
