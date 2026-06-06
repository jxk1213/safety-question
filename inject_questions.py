import re

with open('/Users/jixiaokang/Documents/申通文件/安全月/安全月活动页面（终版）/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

with open('/Users/jixiaokang/Documents/申通文件/安全月/安全月活动页面（终版）/parsed_questions.js', 'r', encoding='utf-8') as f:
    parsed_js = f.read()

# Replace the old QUESTIONS block with the new one
pattern = re.compile(r'const QUESTIONS = \{[\s\S]*?\n\};\n', re.MULTILINE)
new_html = pattern.sub(parsed_js + '\n', html, count=1)

with open('/Users/jixiaokang/Documents/申通文件/安全月/安全月活动页面（终版）/index.html', 'w', encoding='utf-8') as f:
    f.write(new_html)
print("Injection successful!")
