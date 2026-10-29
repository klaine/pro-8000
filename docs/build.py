# Inlines style.css and common.js into each page, so every page is one self-contained file.
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
css = (src / "style.css").read_text()
js = (src / "common.js").read_text()
for page in ["index.html", "how-it-works.html", "sound-design.html"]:
    p = src / page
    if not p.exists():
        continue
    html = p.read_text()
    html = html.replace("<!--STYLE-->", "<style>\n" + css + "</style>")
    html = html.replace("<!--COMMON-->", "<script>\n" + js + "</script>")
    (out / page).write_text(html)
    print(page, len(html))
(out / ".nojekyll").write_text("")
