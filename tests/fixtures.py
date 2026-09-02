"""A trimmed Google Scholar result page used by the parser tests.

The markup keeps the ``gs_*`` structure the parser relies on: two normal cards
(one with a PDF side link), one citation-only card without a title link, the
result-count banner and a next-page link.
"""

from __future__ import annotations

RESULT_PAGE_HTML = """
<html><body>
<div id="gs_ab"><div class="gs_ab_mdw">Articles</div>
<div class="gs_ab_mdw">About 1,240 results (0.06 sec)</div></div>
<div id="gs_res_ccl_mid">

  <div class="gs_r gs_or gs_scl" data-cid="AAA111">
    <div class="gs_ggs gs_fl"><div class="gs_or_ggsm">
      <a href="https://example.org/paper.pdf"><span class="gs_ctg2">[PDF]</span> example.org</a>
    </div></div>
    <div class="gs_ri">
      <h3 class="gs_rt"><span class="gs_ctg2">[PDF]</span>
        <a href="https://example.org/paper">Attention is all you need</a></h3>
      <div class="gs_a">A Vaswani, N Shazeer, N Parmar - Advances in neural information
        processing systems, 2017 - proceedings.neurips.cc</div>
      <div class="gs_rs">We propose a new simple network architecture, the Transformer, based
        solely on attention mechanisms &hellip;</div>
      <div class="gs_fl gs_flb">
        <a href="/scholar?cites=1234567890&amp;as_sdt=2005">Cited by 123,456</a>
        <a href="/scholar?q=related:abc:scholar.google.com/">Related articles</a>
        <a href="/scholar?cluster=1234567890&amp;hl=en">All 89 versions</a>
      </div>
    </div>
  </div>

  <div class="gs_r gs_or gs_scl" data-cid="BBB222">
    <div class="gs_ri">
      <h3 class="gs_rt"><a href="/citations?user=xyz">Deep <b>residual</b> learning for
        multi-<b>agent</b>s</a></h3>
      <div class="gs_a">K He, X Zhang - Proceedings of the IEEE, 2016 - ieee.org</div>
      <div class="gs_rs">Deeper neural networks are more difficult to train &hellip;</div>
      <div class="gs_fl gs_flb"><a href="/scholar?cites=999&amp;as_sdt=2005">Cited by 7</a></div>
    </div>
  </div>

  <div class="gs_r gs_or gs_scl" data-cid="CCC333">
    <div class="gs_ri">
      <h3 class="gs_rt"><span class="gs_ctu">[CITATION]</span> An untraceable monograph</h3>
      <div class="gs_a">J Doe - 1998 - Unknown Press</div>
      <div class="gs_fl gs_flb"><a href="/scholar?cites=555">Cited by 3</a></div>
    </div>
  </div>

</div>
<div id="gs_n"><center><table><tr>
  <td align="left"><a href="/scholar?start=10&amp;q=transformer"><b>Next</b></a></td>
</tr></table></center></div>
</body></html>
"""

EMPTY_PAGE_HTML = """
<html><body><div id="gs_res_ccl_mid">
<div class="gs_med">Your search - zzzqqq - did not match any articles.</div>
</div></body></html>
"""

CAPTCHA_PAGE_HTML = """
<html><head><title>Sorry...</title></head><body>
<div id="gs_captcha_ccl">
  <form id="gs_captcha_f" action="/sorry/index">
    <div class="g-recaptcha" data-sitekey="x"></div>
  </form>
  <p>Our systems have detected unusual traffic from your computer network.</p>
</div></body></html>
"""

CONSENT_PAGE_HTML = """
<html><body><h1>Before you continue to Google</h1>
<button>I agree</button></body></html>
"""
