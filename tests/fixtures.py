"""Trimmed Google Scholar pages used by the parser tests.

The result-page markup keeps the ``gs_*`` structure the parser relies on: two
normal cards (one with a PDF side link), one citation-only card without a title
link, the result-count banner and a next-page link. The profile markup mirrors a
real profile header — an affiliation line with a linked organization, a
verified-email line carrying the homepage link, the interest list — plus the
summary table, publication rows and the "show more" button.
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

AUTHOR_PAGE_HTML = """
<html><body>
<div id="gsc_prf">
  <div id="gsc_prf_i">
    <div id="gsc_prf_in">Ada Lovelace</div>
    <div class="gsc_prf_il">Professor of Analytical Engines,
      <a class="gsc_prf_ila" href="/citations?view_op=view_org&amp;hl=en&amp;org=123">University of London</a>
      , Analytical Society</div>
    <div class="gsc_prf_il" id="gsc_prf_ivh">Verified email at example.edu -
      <a class="gsc_prf_ila" href="https://example.edu/~ada" rel="nofollow">Homepage</a></div>
    <div class="gsc_prf_il" id="gsc_prf_int">
      <a class="gsc_prf_inta"
         href="/citations?view_op=search_authors&amp;mauthors=label:computing">Computing</a>
      <a class="gsc_prf_inta"
         href="/citations?view_op=search_authors&amp;mauthors=label:mathematics">Mathematics</a>
    </div>
  </div>
</div>
<div id="gsc_rsb">
 <table id="gsc_rsb_st"><thead><tr><th></th><th>All</th><th>Since 2021</th></tr></thead>
 <tbody>
  <tr><td class="gsc_rsb_sc1">Citations</td>
      <td class="gsc_rsb_std">12,345</td><td class="gsc_rsb_std">4,321</td></tr>
  <tr><td class="gsc_rsb_sc1">h-index</td><td class="gsc_rsb_std">57</td><td class="gsc_rsb_std">40</td></tr>
  <tr><td class="gsc_rsb_sc1">i10-index</td>
      <td class="gsc_rsb_std">120</td><td class="gsc_rsb_std">98</td></tr>
 </tbody></table>
</div>
<table id="gsc_a_t"><tbody id="gsc_a_b">
  <tr class="gsc_a_tr">
    <td class="gsc_a_t">
      <a href="/citations?view_op=view_citation&amp;hl=en&amp;user=AAAAAAAAAAAA&amp;\
citation_for_view=AAAAAAAAAAAA:u5HHmVD_uO8C"
         class="gsc_a_at">Notes on the Analytical Engine</a>
      <div class="gs_gray">A Lovelace, C Babbage</div>
      <div class="gs_gray">Scientific Memoirs 3, 666-731, 1843</div>
    </td>
    <td class="gsc_a_c">
      <a href="/scholar?oi=bibs&amp;hl=en&amp;cites=111222333" class="gsc_a_ac gs_ibl">2,048</a></td>
    <td class="gsc_a_y"><span class="gsc_a_h gsc_a_hc gs_ibl">1843</span></td>
  </tr>
  <tr class="gsc_a_tr">
    <td class="gsc_a_t">
      <a href="/citations?view_op=view_citation&amp;hl=en&amp;user=AAAAAAAAAAAA&amp;\
citation_for_view=AAAAAAAAAAAA:2osOgNQ5qMEC"
         class="gsc_a_at">An uncited draft</a>
      <div class="gs_gray">A Lovelace</div>
      <div class="gs_gray">Unpublished manuscript</div>
    </td>
    <td class="gsc_a_c"><a href="" class="gsc_a_ac gs_ibl gsc_a_acm"></a></td>
    <td class="gsc_a_y"><span class="gsc_a_h gsc_a_hc gs_ibl"></span></td>
  </tr>
</tbody></table>
<button id="gsc_bpf_more" type="button" onclick="void(0)"><span>Show more</span></button>
</body></html>
"""

AUTHOR_LAST_PAGE_HTML = AUTHOR_PAGE_HTML.replace(
    '<button id="gsc_bpf_more" type="button" onclick="void(0)">',
    '<button id="gsc_bpf_more" type="button" disabled="">',
)


CITE_POPUP_HTML = """
<html><body>
<div id="gs_citt">
  <table><tbody>
    <tr><th scope="row" class="gs_cith">MLA</th>
        <td><div tabindex="0" class="gs_citr">Lovelace, Ada. "Notes on the Analytical Engine."
        <i>Scientific Memoirs</i> 3 (1843): 666-731.</div></td></tr>
  </tbody></table>
</div>
<div id="gs_citi">
  <a class="gs_citi" href="https://scholar.googleusercontent.com/scholar.bib?q=info:CID:\
scholar.google.com/&amp;output=citation&amp;scisig=SIG&amp;scisf=4&amp;ct=citation&amp;cd=-1&amp;hl=en"
     target="_blank" onclick="return gs_ocit(event,'CID','0')">BibTeX</a>
  <a class="gs_citi" href="https://scholar.googleusercontent.com/scholar.enw?q=info:CID:\
scholar.google.com/&amp;output=citation&amp;scisig=SIG&amp;scisf=3&amp;ct=citation&amp;cd=-1&amp;hl=en"
     target="_blank">EndNote</a>
</div>
</body></html>
"""

BIBTEX_EXPORT_HTML = """
<html><head><meta name="color-scheme" content="light dark"></head>
<body><pre style="word-wrap: break-word; white-space: pre-wrap;">@article{lovelace1843notes,
  title={Notes on the Analytical Engine},
  author={Lovelace, Ada},
  journal={Scientific Memoirs},
  volume={3},
  pages={666--731},
  year={1843}
}
</pre></body></html>
"""


def result_page_html(cards: int, *, next_start: int | None = 10) -> str:
    """Build a result page carrying an exact number of cards.

    Page-budget arithmetic depends on how many records a page holds, so tests that compare
    an estimate with the crawl loop need a page as full as Scholar's.

    :param cards: how many result cards the page carries.
    :param next_start: offset the next-page link points at; None omits the link.
    :returns: the page HTML.
    """
    body = "".join(
        f"""
  <div class="gs_r gs_or gs_scl" data-cid="ID{index}">
    <div class="gs_ri">
      <h3 class="gs_rt"><a href="https://example.org/{index}">Paper {index}</a></h3>
      <div class="gs_a">A Author, B Author - A Journal, 2020 - example.org</div>
      <div class="gs_rs">A snippet.</div>
      <div class="gs_fl gs_flb"><a href="/scholar?cites=100{index}">Cited by 5</a></div>
    </div>
  </div>
"""
        for index in range(cards)
    )
    nav = (
        '<div id="gs_n"><center><table><tr><td align="left">'
        f'<a href="/scholar?start={next_start}&amp;q=x"><b>Next</b></a>'
        "</td></tr></table></center></div>"
        if next_start is not None
        else ""
    )
    return (
        '<html><body><div id="gs_ab"><div class="gs_ab_mdw">About 1,240 results (0.06 sec)</div></div>'
        f'<div id="gs_res_ccl_mid">{body}</div>{nav}</body></html>'
    )
