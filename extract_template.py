"""Run once to extract the HTML template from the view-source file."""
from html.parser import HTMLParser
import os

class CodeExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_line_content = False
        self.lines = []
        self.current_line = []

    def handle_starttag(self, tag, attrs):
        if tag == 'td' and dict(attrs).get('class') == 'line-content':
            self.in_line_content = True
            self.current_line = []

    def handle_endtag(self, tag):
        if tag == 'td' and self.in_line_content:
            self.in_line_content = False
            self.lines.append(''.join(self.current_line))

    def handle_data(self, data):
        if self.in_line_content:
            self.current_line.append(data)

base = os.path.dirname(__file__)
src = os.path.join(base, 'view-source_https___www.benuestateschools.com_dhis2_dhis2_sync.php.html')

with open(src, 'r', encoding='utf-8') as f:
    content = f.read()

parser = CodeExtractor()
parser.feed(content)

html = '\n'.join(parser.lines)

# Point AJAX calls at Flask endpoint instead of PHP file
html = html.replace("const AJAX = 'dhis2_ajax.php';", "const AJAX = '/ajax';")

os.makedirs(os.path.join(base, 'templates'), exist_ok=True)
out = os.path.join(base, 'templates', 'index.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"Done. {len(parser.lines)} lines written to templates/index.html")
ajax_check = "const AJAX = '/ajax';" in html
print(f"AJAX endpoint updated: {ajax_check}")
