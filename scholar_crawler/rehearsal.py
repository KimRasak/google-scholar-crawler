"""Handoff rehearsal: exercising the human-takeover path without touching Scholar.

The takeover path is the one part of the crawler a unit test cannot fully prove: it
depends on a real window, a bell the operator can hear, and a page that stops looking
challenged once a person acts. The rehearsal drives that whole path against a local page
rendered in the crawler's own browser, so no request reaches Google and no real challenge
has to be provoked.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page

from .challenge import ChallengeUnattended, HumanHandoff, detect_challenge
from .storage import ChallengeLog

REHEARSAL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Handoff rehearsal</title>
<style>
 body { font: 15px system-ui, sans-serif; margin: 48px; max-width: 34em; }
 .note { color: #666; font-size: 13px; }
 button { font-size: 15px; padding: 8px 14px; margin-top: 18px; }
</style></head>
<body>
<h2>Handoff rehearsal</h2>
<p class="note">This is a local page. Nothing was requested from Google, and this is
not a real challenge — it exists to prove the takeover path works end to end.</p>
<form id="captcha-form" onsubmit="return false;">
  <p>To continue, confirm you are not a robot. (I'm not a robot)</p>
  <button id="rehearsal-clear" type="button">Pretend the challenge is solved</button>
</form>
<script>
document.getElementById('rehearsal-clear').addEventListener('click', function () {
  document.body.innerHTML =
    '<div id="gs_res_ccl_mid"><div class="gs_r gs_or gs_scl">' +
    '<h3 class="gs_rt"><a href="#">Rehearsal result</a></h3>' +
    '<div class="gs_a">the crawler treats this page as Scholar content again</div>' +
    '</div></div>';
});
</script>
</body></html>
"""
"""Local stand-in for a challenge page: detected as a CAPTCHA until its button is
pressed, after which it satisfies :data:`~scholar_crawler.challenge.RESULTS_SELECTOR`."""


def rehearse(page: Page, handoff: HumanHandoff, log: ChallengeLog | None = None) -> bool:
    """Drive detection, takeover and resume against the local rehearsal page.

    :param page: a page from the crawler's own browser session; its content is replaced.
    :param handoff: the takeover policy under test, with the run's real timeout.
    :param log: when set, the rehearsed takeover is recorded exactly as a real one would be,
        which also proves the log is writable before a real challenge depends on it.
    :returns: True when the challenge was detected, handed over and cleared.
    :raises ChallengeUnattended: when the handoff refuses to wait or the wait times out.
    """
    page.set_content(REHEARSAL_HTML)
    challenge = detect_challenge(page)
    if challenge is None:
        print(
            "[rehearse] the rehearsal page was not recognised as a challenge, "
            "so detection would miss a real one",
            flush=True,
        )
        return False
    print(f"[rehearse] detected {challenge.kind.value}: {challenge.detail}", flush=True)
    started = time.monotonic()
    outcome = "rehearsed"
    saw: tuple[str, ...] = (challenge.kind.value,)
    try:
        saw = handoff.resolve(page, challenge).saw
    except ChallengeUnattended:
        outcome = "unattended"
        raise
    except KeyboardInterrupt:
        outcome = "interrupted"
        raise
    finally:
        waited = time.monotonic() - started
        if log is not None:
            entry = log.record(
                kind=challenge.kind.value,
                url=challenge.url,
                reason=challenge.detail,
                request_index=0,
                consecutive=1,
                waited=waited,
                outcome=outcome,
                target="rehearsal",
                saw=saw,
            )
            print(f"[rehearse] recorded -> {log.path}: {entry.describe()}", flush=True)
    if detect_challenge(page) is not None:
        print("[rehearse] the page still looks challenged after the wait returned", flush=True)
        return False
    print(
        f"[rehearse] takeover completed after {waited:.1f}s; a real crawl would resume here",
        flush=True,
    )
    return True
