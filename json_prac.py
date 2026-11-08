
import json

s = '{"name": "Asha", "age": 25, "skills": ["python", "sql"]}'

data=json.loads(s)
json.dumps(data, indent=4)                  # pretty print
json.dumps(data, separators=(",", ":"))     # compact
json.dumps({"n": "é"}, ensure_ascii=False)  # keep unicode
#json.dumps(obj, default=str)                # handle datetime, etc.
