"""Headers that tell a browser what a response may do: sent on every response, plus a
content security policy for each of the two pages the server serves itself."""
import base64
import hashlib
import re
from pathlib import Path

# On every HTTP response. The pages are never meant to be framed by anyone.
EVERY_RESPONSE = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
HTTPS_ONLY = {"Strict-Transport-Security": "max-age=31536000"}  # only where https is certain
INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)


def page_policy(page: Path, **sources: str) -> dict[str, str]:
    """A Content-Security-Policy header for `page`: nothing is allowed except the page's own
    inline scripts (by hash, so an injected script does not run), inline styles, calls back to
    this site, and what `sources` adds, e.g. script_src="https://example.com/x.js"."""
    # A browser hashes the script as parsed, and parsing turns every line ending into \n.
    text = page.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode() + "'"
        for body in INLINE_SCRIPT.findall(text)
    )
    policy = {
        "default-src": "'none'",
        "script-src": hashes,
        "style-src": "'unsafe-inline'",
        "connect-src": "'self'",
        "base-uri": "'none'",
        "form-action": "'none'",
        "frame-ancestors": "'none'",
    }
    for name, extra in sources.items():
        directive = name.replace("_", "-")
        policy[directive] = f"{policy[directive]} {extra}" if directive in policy else extra
    return {"Content-Security-Policy": "; ".join(f"{name} {value}" for name, value in policy.items())}
