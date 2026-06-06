import pandas as pd
import json

df = pd.read_excel('/Users/jixiaokang/Documents/申通文件/安全月/安全月活动页面（终版）/安全月知识竞赛初赛题库.xlsx', skiprows=1)

result = {
    'judge': [],
    'single': [],
    'multi': [],
    'uncertain': []
}

def get_answer_indices(ans_str):
    ans_str = str(ans_str).strip().upper()
    mapping = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    res = []
    for char in ans_str:
        if char in mapping:
            res.append(mapping[char])
    return res

for idx, row in df.iterrows():
    q_type = str(row['题型']).strip()
    q_text = str(row['题干']).strip()
    ans = str(row['答案']).strip()
    
    opts = []
    for col in ['选项A', '选项B', '选项C', '选项D', '选项E']:
        if pd.notna(row[col]) and str(row[col]).strip() != '':
            opts.append(str(row[col]).strip())
            
    if q_type == '判断':
        # "正确" is usually A, "错误" is usually B
        is_true = ('A' in ans.upper())
        result['judge'].append({ 'q': q_text, 'answer': is_true })
    elif q_type == '单选':
        ans_idx = get_answer_indices(ans)[0] if get_answer_indices(ans) else 0
        result['single'].append({ 'q': q_text, 'options': opts, 'answer': ans_idx })
    elif q_type == '多选':
        result['multi'].append({ 'q': q_text, 'options': opts, 'answer': get_answer_indices(ans) })
    elif q_type == '不定项':
        result['uncertain'].append({ 'q': q_text, 'options': opts, 'answer': get_answer_indices(ans) })

js_output = "const QUESTIONS = " + json.dumps(result, ensure_ascii=False, indent=2) + ";"

with open('/Users/jixiaokang/Documents/申通文件/安全月/安全月活动页面（终版）/parsed_questions.js', 'w', encoding='utf-8') as f:
    f.write(js_output)

print("Parsed successfully!")
